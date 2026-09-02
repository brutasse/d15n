import time

import pytest

from d15n import parallel, runner
from d15n import schedule, step, workflow
from d15n.errors import SimulatedCrash
from d15n.models import Step, Workflow
from d15n.runner import execute
from tests.helpers import claim_next, crash_on, re_claim, run_to_completion

pytestmark = pytest.mark.django_db(transaction=True)

CALLS = []


@pytest.fixture(autouse=True)
def _calls():
    CALLS.clear()
    yield


@step
def slow_a():
    CALLS.append("slow_a")
    time.sleep(0.3)
    return "a"


@step
def slow_b():
    CALLS.append("slow_b")
    time.sleep(0.3)
    return "b"


@workflow
def both(args):
    start = time.monotonic()
    a, b = parallel(slow_a, slow_b)
    return {"a": a, "b": b, "elapsed": time.monotonic() - start}


def test_parallel_branches_run_concurrently():
    wf = run_to_completion(both, {})
    assert wf.result["a"] == "a"
    assert wf.result["b"] == "b"
    assert wf.result["elapsed"] < 0.5
    assert set(CALLS) == {"slow_a", "slow_b"}
    rows = {s.step_id: s.name for s in Step.objects.filter(workflow_id=wf.id)}
    assert rows == {
        "1.0.1": f"{__name__}.slow_a",
        "1.1.1": f"{__name__}.slow_b",
    }


class Boom(Exception):
    pass


@step
def ok():
    CALLS.append("ok")
    return "ok"


@step
def boom():
    CALLS.append("boom")
    raise Boom("branch blew up")


@workflow
def one_fails(args):
    a, b = parallel(ok, boom)
    return [a, b]


def test_parallel_single_failure_reraises_original():
    wf = schedule(one_fails, {})
    claim_next()
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.FAILED
    assert wf.error["type"] == f"{Boom.__module__}.{Boom.__qualname__}"
    assert wf.error["message"] == "branch blew up"
    # The healthy branch still completed and was recorded.
    rows = {s.step_id: s.status for s in Step.objects.filter(workflow_id=wf.id)}
    assert rows == {"1.0.1": Step.Status.DONE, "1.1.1": Step.Status.FAILED}


@step
def boom_two():
    raise ValueError("second boom")


@workflow
def both_fail(args):
    x, y = parallel(boom, boom_two)
    return [x, y]


def test_parallel_multiple_failures_raise_exception_group():
    wf = schedule(both_fail, {})
    claim_next()
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.FAILED
    assert wf.error["type"].endswith("ExceptionGroup")


@step
def branch_one():
    CALLS.append("branch_one")
    return "1"


@step
def branch_two():
    CALLS.append("branch_two")
    return "2"


@step
def tail():
    CALLS.append("tail")
    return "t"


@workflow
def forked(args):
    x, y = parallel(branch_one, branch_two)
    return x + y + tail()


def test_parallel_branch_recovers_after_crash():
    wf = schedule(forked, {})
    claim_next()

    runner.fault = crash_on("1.0.1")
    with pytest.raises(SimulatedCrash):
        execute(wf.id)
    runner.fault = None

    # branch_one's side effect happened but its outcome was not recorded.
    assert CALLS.count("branch_one") == 1

    re_claim(wf)
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.COMPLETED
    assert wf.result == "12t"
    assert CALLS.count("branch_one") == 2
    assert CALLS.count("branch_two") in (1, 2)
    assert CALLS.count("tail") == 1
