"""Worker loop: claim due workflows and execute them on a thread pool.

A worker has a stable name that is identical across restarts. On startup it
re-claims the workflows it was running when it last went away (matched by
name); in steady state it only claims new scheduled workflows.

On SIGTERM/SIGINT the worker stops claiming and drains: the step in flight
in each workflow finishes and is recorded, but no new step starts, so the
workflows are left running for the next worker with the same name. If the
in-flight work does not finish within `drain` seconds, the process exits,
orphaning whatever is still running.
"""

import logging
import os
import socket
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

from django.db import connection, connections, transaction
from django.utils import timezone

from everystep import metrics, serde
from everystep.errors import DrainOrphan, SimulatedCrash, Terminal
from everystep.models import Workflow
from everystep.runner import execute
from everystep.telemetry import report_workflow_failure

logger = logging.getLogger("everystep")


def _lock_clause():
    """Row-locking clause for the claim SELECT, chosen by backend.

    PostgreSQL uses SKIP LOCKED so concurrent workers grab disjoint batches
    without blocking each other. Other backends (MariaDB) have no SKIP
    LOCKED, so a plain FOR UPDATE is used: claims stay exclusive, they just
    serialize while a batch is being locked.
    """
    if connection.vendor == "postgresql":
        return "FOR UPDATE SKIP LOCKED"
    return "FOR UPDATE"


def _select_ids(where, params, limit):
    table = Workflow._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id
            FROM {table}
            WHERE {where}
            ORDER BY created_at
            LIMIT %s
            {_lock_clause()}
            """,
            [*params, limit],
        )
        return [row[0] for row in cursor.fetchall()]


def claim_new(limit, name):
    """Claim up to `limit` scheduled workflows for this runner.

    Uses SELECT ... FOR UPDATE SKIP LOCKED on PostgreSQL so concurrent
    runners claim disjoint sets without blocking; on other backends (e.g.
    MariaDB) a plain FOR UPDATE keeps claims exclusive but serializes them
    while a batch is being locked.
    """
    with transaction.atomic():
        ids = _select_ids(
            "status = %s", [Workflow.Status.SCHEDULED], limit
        )
        if ids:
            Workflow.objects.filter(id__in=ids).update(
                status=Workflow.Status.RUNNING,
                claimed_by=name,
            )
    if not ids:
        return []
    return list(Workflow.objects.filter(id__in=ids))


def resume_own(limit, name):
    """Claim back this runner's own in-flight workflows after a restart.

    Matches running workflows whose claimed_by is this runner's name. Called
    once at startup; a runner never holds more than its pool size in flight.
    """
    with transaction.atomic():
        ids = _select_ids(
            "status = %s AND claimed_by = %s",
            [Workflow.Status.RUNNING, name],
            limit,
        )
    if not ids:
        return []
    return list(Workflow.objects.filter(id__in=ids))


class Worker:
    """Claim due workflows and execute them on a thread pool.

    See the running section of the documentation for the claim loop, the
    stable-name contract, and the SIGTERM drain behavior.
    """

    def __init__(
        self, pool_size=4, poll=0.2, name=None, drain=30, metrics_port=0, metrics_bind="0.0.0.0"
    ):
        self.pool_size = pool_size
        self.poll = poll
        self.name = name or socket.gethostname()
        self.drain = drain
        self.metrics_port = metrics_port
        self.metrics_bind = metrics_bind
        self._stop = threading.Event()
        self._draining = threading.Event()
        self._futures = []
        self._executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="everystep-w")
        self._metrics_server = None

    def stop(self):
        self._stop.set()
        self._draining.set()

    def run(self):
        _install_signal_handlers(self)
        try:
            metrics.worker_started(self.name, self.pool_size)
            self._start_metrics_server()
            self._catchup()
            while not self._stop.is_set():
                self._reap()
                capacity = self.pool_size - len(self._futures)
                if capacity > 0:
                    claimed = claim_new(capacity, self.name)
                    for workflow in claimed:
                        self._futures.append(self._executor.submit(self._execute, workflow))
                    metrics.record_claims(self.name, len(claimed))
                metrics.set_inflight(self.name, len(self._futures))
                self._stop.wait(self.poll)
        finally:
            leftovers = self._drain()
            if leftovers:
                metrics.record_orphans(self.name, len(leftovers))
                logger.warning(
                    "everystep worker: drain deadline of %ss expired with %d workflow(s) still "
                    "in flight; they are orphaned and will be picked up by the next worker "
                    "named %r",
                    self.drain, len(leftovers), self.name,
                )
                if threading.current_thread() is threading.main_thread():
                    # Standalone worker: exit now rather than let the
                    # interpreter join the abandoned pool threads at
                    # shutdown. An embedded worker just returns; the host
                    # process owns its own lifecycle.
                    os._exit(0)
            metrics.set_inflight(self.name, 0)
            if self._metrics_server is not None:
                self._metrics_server.server_close()
            connections.close_all()

    def _start_metrics_server(self):
        if not self.metrics_port:
            return
        self._metrics_server = metrics.start_http_server(self.metrics_port, self.metrics_bind)

    def _drain(self):
        """Stop queued work and wait up to `self.drain` seconds for the
        in-flight workflows to finish. Returns the futures still running
        when the deadline is hit. With drain of 0, waits indefinitely and
        returns nothing."""
        if not self.drain:
            self._executor.shutdown(wait=True)
            return []
        self._executor.shutdown(wait=False, cancel_futures=True)
        _, not_done = wait(self._futures, timeout=self.drain)
        return list(not_done)

    def _catchup(self):
        """Re-claim this runner's in-flight workflows left over from before.

        Runs once at startup, before the poll loop, so it cannot re-select
        workflows this process is already executing in its pool.
        """
        workflows = resume_own(self.pool_size, self.name)
        for workflow in workflows:
            self._futures.append(self._executor.submit(self._execute, workflow))
        metrics.record_claims(self.name, len(workflows))

    def _reap(self):
        pending = []
        for future in self._futures:
            if future.done():
                exc = future.exception()
                if exc is not None:
                    logger.exception("everystep worker: unexpected worker failure: %s", exc)
            else:
                pending.append(future)
        self._futures = pending

    def _execute(self, workflow):
        started = time.monotonic()
        try:
            execute(workflow.id, draining=self._draining)
        except Workflow.DoesNotExist:
            logger.warning("everystep worker: workflow %s no longer exists", workflow.id)
        except Exception as exc:
            logger.exception("everystep worker: workflow %s crashed outside the runner", workflow.id)
            report_workflow_failure(exc, workflow_id=workflow.id)
            try:
                updated = Workflow.objects.filter(id=workflow.id, status=Workflow.Status.RUNNING).update(
                    status=Workflow.Status.FAILED,
                    error=serde.encode_exception(exc),
                    completed_at=timezone.now(),
                )
                if updated and not isinstance(exc, (SimulatedCrash, DrainOrphan, Terminal)):
                    metrics.record_workflow_terminal(
                        workflow.name, "failed", time.monotonic() - started
                    )
            except Exception:
                logger.exception("everystep worker: could not mark workflow %s failed", workflow.id)
        finally:
            # Pool threads are long-lived and Django connections are
            # thread-local, so release this thread's connection to avoid
            # leaking one per executed workflow.
            connections.close_all()


def _install_signal_handlers(worker):
    if threading.current_thread() is not threading.main_thread():
        return
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signum, lambda *_args: worker.stop())
        except (ValueError, OSError):
            return
