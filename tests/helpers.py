from datetime import timedelta

from django.utils import timezone

from d15n import schedule
from d15n import runner
from d15n.errors import SimulatedCrash
from d15n.models import Workflow
from d15n.runner import execute
from d15n.worker import claim

TEST_LEASE = 300


def run_to_completion(*args, **kwargs):
    """Schedule, claim and execute a workflow; return the completed instance."""
    run = schedule(*args, **kwargs)
    claimed = claim(1, TEST_LEASE, "test-worker")
    assert [w.id for w in claimed] == [run.id], f"claimed {[w.id for w in claimed]}"
    execute(run.id)
    run.refresh_from_db()
    return run


def claim_next(worker="test-worker"):
    claimed = claim(1, TEST_LEASE, worker)
    assert len(claimed) == 1, f"expected one claimable workflow, got {[w.id for w in claimed]}"
    return claimed[0]


def re_claim(run, worker="test-worker-2"):
    """Expire the lease and claim the workflow with another worker."""
    Workflow.objects.filter(id=run.id).update(
        claimed_at=timezone.now() - timedelta(seconds=TEST_LEASE + 1)
    )
    return claim_next(worker)


def crash_on(step_id):
    """Fault handler simulating a process death before `step_id` is recorded."""

    def handler(ctx, current_step_id):
        if current_step_id == step_id:
            raise SimulatedCrash()

    return handler
