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
from concurrent.futures import ThreadPoolExecutor, wait

from django.db import connection, connections, transaction
from django.utils import timezone

from d15n import serde
from d15n.models import Workflow
from d15n.runner import execute

logger = logging.getLogger("d15n")


def _check_vendor():
    if connection.vendor != "postgresql":
        raise RuntimeError("d15n workers require PostgreSQL (FOR UPDATE SKIP LOCKED)")


def _select_ids(where, params, limit):
    _check_vendor()
    table = Workflow._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id
            FROM {table}
            WHERE {where}
            ORDER BY created_at
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            [*params, limit],
        )
        return [row[0] for row in cursor.fetchall()]


def claim_new(limit, name):
    """Claim up to `limit` scheduled workflows for this runner.

    Uses SELECT ... FOR UPDATE SKIP LOCKED so concurrent runners claim
    disjoint sets. Requires PostgreSQL.
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
    def __init__(self, pool_size=4, poll=0.2, name=None, drain=30):
        self.pool_size = pool_size
        self.poll = poll
        self.name = name or socket.gethostname()
        self.drain = drain
        self._stop = threading.Event()
        self._draining = threading.Event()
        self._futures = []
        self._executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="d15n-w")

    def stop(self):
        self._stop.set()
        self._draining.set()

    def run(self):
        _install_signal_handlers(self)
        try:
            self._catchup()
            while not self._stop.is_set():
                self._reap()
                capacity = self.pool_size - len(self._futures)
                if capacity > 0:
                    for workflow in claim_new(capacity, self.name):
                        self._futures.append(self._executor.submit(self._execute, workflow.id))
                self._stop.wait(self.poll)
        finally:
            leftovers = self._drain()
            if leftovers:
                logger.warning(
                    "d15n worker: drain deadline of %ss expired with %d workflow(s) still "
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
            connections.close_all()

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
        for workflow in resume_own(self.pool_size, self.name):
            self._futures.append(self._executor.submit(self._execute, workflow.id))

    def _reap(self):
        pending = []
        for future in self._futures:
            if future.done():
                exc = future.exception()
                if exc is not None:
                    logger.exception("d15n worker: unexpected worker failure: %s", exc)
            else:
                pending.append(future)
        self._futures = pending

    def _execute(self, workflow_id):
        try:
            execute(workflow_id, draining=self._draining)
        except Workflow.DoesNotExist:
            logger.warning("d15n worker: workflow %s no longer exists", workflow_id)
        except Exception as exc:
            logger.exception("d15n worker: workflow %s crashed outside the runner", workflow_id)
            try:
                Workflow.objects.filter(id=workflow_id, status=Workflow.Status.RUNNING).update(
                    status=Workflow.Status.FAILED,
                    error=serde.encode_exception(exc),
                    completed_at=timezone.now(),
                )
            except Exception:
                logger.exception("d15n worker: could not mark workflow %s failed", workflow_id)
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
