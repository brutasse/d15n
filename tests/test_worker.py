import threading
import time
from datetime import timedelta

import pytest

from django.utils import timezone

from d15n import schedule, step, workflow
from d15n.models import Workflow
from d15n.worker import Worker, claim
from tests.helpers import TEST_LEASE

pytestmark = pytest.mark.django_db(transaction=True)


@step
def noop():
    return None


@workflow
def simple(args):
    noop()
    return "done"


def test_claim_is_exclusive_across_workers():
    for _ in range(6):
        schedule(simple, {})
    results = []
    barrier = threading.Barrier(3)

    def worker(index):
        barrier.wait()
        results.append([w.id for w in claim(3, TEST_LEASE, f"worker-{index}")])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    claimed = [wf_id for ids in results for wf_id in ids]
    assert len(claimed) == 6
    assert len(set(claimed)) == 6


def test_fresh_lease_not_reclaimed():
    wf = schedule(simple, {})
    assert [w.id for w in claim(1, TEST_LEASE, "w1")] == [wf.id]
    assert claim(1, TEST_LEASE, "w2") == []


def test_stale_lease_reclaimed():
    wf = schedule(simple, {})
    claim(1, TEST_LEASE, "w1")
    Workflow.objects.filter(id=wf.id).update(
        claimed_at=timezone.now() - timedelta(seconds=TEST_LEASE + 1)
    )
    claimed = claim(1, TEST_LEASE, "w2")
    assert [w.id for w in claimed] == [wf.id]
    assert claimed[0].claimed_by == "w2"
    assert claimed[0].status == Workflow.Status.RUNNING


def test_worker_loop_completes_due_workflows():
    for _ in range(2):
        schedule(simple, {})

    worker = Worker(pool_size=2, poll=0.05, lease_seconds=TEST_LEASE)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if Workflow.objects.filter(status=Workflow.Status.COMPLETED).count() == 2:
                break
            time.sleep(0.05)
    finally:
        worker.stop()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert Workflow.objects.filter(status=Workflow.Status.COMPLETED).count() == 2


def test_command_is_registered():
    from django.core.management import get_commands

    assert "d15n_worker" in get_commands()
