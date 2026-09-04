import threading
import time

import pytest

from d15n import schedule, step, workflow
from d15n.models import Workflow
from d15n.worker import Worker, claim_new, resume_own

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
        results.append([w.id for w in claim_new(3, f"worker-{index}")])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    claimed = [wf_id for ids in results for wf_id in ids]
    assert len(claimed) == 6
    assert len(set(claimed)) == 6


def test_other_runner_cannot_claim_or_reclaim():
    wf = schedule(simple, {})
    assert [w.id for w in claim_new(1, "w1")] == [wf.id]
    # w1's running workflow is invisible to w2: neither as new work nor as a
    # restart of w2's own in-flight work.
    assert claim_new(1, "w2") == []
    assert resume_own(1, "w2") == []


def test_runner_reclaims_own_workflows_after_restart():
    wf = schedule(simple, {})
    claim_new(1, "w1")  # now running, claimed_by="w1"
    claimed = resume_own(1, "w1")
    assert [w.id for w in claimed] == [wf.id]
    assert claimed[0].claimed_by == "w1"
    assert claimed[0].status == Workflow.Status.RUNNING


def test_worker_catchup_resumes_own_workflows():
    # A workflow w1 was running when it went away; a fresh process with the
    # same name picks it up at startup and completes it.
    wf = schedule(simple, {})
    claim_new(1, "w1")

    worker = Worker(pool_size=1, poll=0.05, name="w1")
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            wf.refresh_from_db()
            if wf.status == Workflow.Status.COMPLETED:
                break
            time.sleep(0.05)
    finally:
        worker.stop()
    thread.join(timeout=10)
    assert not thread.is_alive()
    wf.refresh_from_db()
    assert wf.status == Workflow.Status.COMPLETED


def test_worker_loop_completes_due_workflows():
    for _ in range(2):
        schedule(simple, {})

    worker = Worker(pool_size=2, poll=0.05, name="test-worker")
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
