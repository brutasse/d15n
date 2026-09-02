"""Worker loop: claim due workflows and execute them on a thread pool."""

import logging
import os
import socket
import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from d15n import serde
from d15n.models import Workflow
from d15n.runner import execute

logger = logging.getLogger("d15n")


def claim(limit, lease_seconds, worker_id):
    """Claim up to `limit` due workflows for this worker.

    Due means: scheduled, or running with an expired lease. Uses
    SELECT ... FOR UPDATE SKIP LOCKED so concurrent workers claim disjoint
    sets. Requires PostgreSQL.
    """
    if connection.vendor != "postgresql":
        raise RuntimeError("d15n workers require PostgreSQL (FOR UPDATE SKIP LOCKED)")
    table = Workflow._meta.db_table
    stale_before = timezone.now() - timedelta(seconds=lease_seconds)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id
                FROM {table}
                WHERE status = %s
                   OR (status = %s AND claimed_at IS NOT NULL AND claimed_at <= %s)
                ORDER BY created_at
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                [Workflow.Status.SCHEDULED, Workflow.Status.RUNNING, stale_before, limit],
            )
            ids = [row[0] for row in cursor.fetchall()]
        if ids:
            Workflow.objects.filter(id__in=ids).update(
                status=Workflow.Status.RUNNING,
                claimed_by=worker_id,
                claimed_at=timezone.now(),
            )
    if not ids:
        return []
    return list(Workflow.objects.filter(id__in=ids))


class Worker:
    def __init__(self, pool_size=4, poll=0.2, lease_seconds=300):
        self.pool_size = pool_size
        self.poll = poll
        self.lease_seconds = lease_seconds
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self._stop = threading.Event()
        self._futures = []
        self._executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="d15n-w")

    def stop(self):
        self._stop.set()

    def run(self):
        _install_signal_handlers(self)
        try:
            while not self._stop.is_set():
                self._reap()
                capacity = self.pool_size - len(self._futures)
                if capacity > 0:
                    for workflow in claim(capacity, self.lease_seconds, self.worker_id):
                        self._futures.append(self._executor.submit(self._execute, workflow.id))
                self._stop.wait(self.poll)
        finally:
            self._executor.shutdown(wait=True)

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
            execute(workflow_id)
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


def _install_signal_handlers(worker):
    if threading.current_thread() is not threading.main_thread():
        return
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signum, lambda *_args: worker.stop())
        except (ValueError, OSError):
            return
