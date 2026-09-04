from d15n import schedule
from d15n.errors import SimulatedCrash
from d15n.runner import execute
from d15n.worker import claim_new, resume_own


def run_to_completion(*args, **kwargs):
    """Schedule, claim and execute a workflow; return the completed instance."""
    run = schedule(*args, **kwargs)
    claimed = claim_new(1, "test-worker")
    assert [w.id for w in claimed] == [run.id], f"claimed {[w.id for w in claimed]}"
    execute(run.id)
    run.refresh_from_db()
    return run


def claim_next(worker="test-worker"):
    claimed = claim_new(1, worker)
    assert len(claimed) == 1, f"expected one claimable workflow, got {[w.id for w in claimed]}"
    return claimed[0]


def re_claim(run):
    """Simulate the claiming runner restarting and re-claiming its workflow."""
    run.refresh_from_db()
    claimed = resume_own(1, run.claimed_by)
    assert [w.id for w in claimed] == [run.id], f"expected to re-claim {run.id}, got {[w.id for w in claimed]}"
    return claimed[0]


def crash_on(step_id):
    """Fault handler simulating a process death before `step_id` is recorded."""

    def handler(ctx, current_step_id):
        if current_step_id == step_id:
            raise SimulatedCrash()

    return handler
