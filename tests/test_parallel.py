import time

import pytest

from everystep import parallel, runner
from everystep import schedule, step, workflow
from everystep.errors import SimulatedCrash
from everystep.models import Step, Workflow
from everystep.runner import execute
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


@step
def seq_first():
    CALLS.append("seq_first")
    return "f"


@step
def seq_second():
    CALLS.append("seq_second")
    return "s"


@workflow
def seq_branch(args):
    (a, b), c = parallel([seq_first, seq_second], branch_two)
    return (a, b, c)


def test_sequence_branch_runs_in_order_and_returns_tuple():
    wf = run_to_completion(seq_branch, {})
    # Tuples are persisted as JSON arrays.
    assert wf.result == ["f", "s", "2"]
    rows = {s.step_id: s.name for s in Step.objects.filter(workflow_id=wf.id)}
    assert rows == {
        "1.0.1": f"{__name__}.seq_first",
        "1.0.2": f"{__name__}.seq_second",
        "1.1.1": f"{__name__}.branch_two",
    }
    assert CALLS.index("seq_first") < CALLS.index("seq_second")


@workflow
def slow_seq(args):
    start = time.monotonic()
    parallel([slow_a, slow_b], slow_b)
    return time.monotonic() - start


def test_sequence_branch_runs_concurrently_with_siblings():
    wf = run_to_completion(slow_seq, {})
    # The sequence branch takes ~0.6s (two 0.3s steps in order); a fully
    # serial run would take 0.9s.
    assert wf.result < 0.8


@workflow
def seq_fails(args):
    parallel([ok, boom], branch_one)
    return "unreached"


def test_sequence_failure_records_prior_steps():
    wf = schedule(seq_fails, {})
    claim_next()
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.FAILED
    assert wf.error["type"] == f"{Boom.__module__}.{Boom.__qualname__}"
    assert wf.error["message"] == "branch blew up"
    rows = {s.step_id: s.status for s in Step.objects.filter(workflow_id=wf.id)}
    assert rows == {
        "1.0.1": Step.Status.DONE,
        "1.0.2": Step.Status.FAILED,
        "1.1.1": Step.Status.DONE,
    }


def test_sequence_branch_recovers_after_crash():
    wf = schedule(seq_branch, {})
    claim_next()

    runner.fault = crash_on("1.0.2")
    with pytest.raises(SimulatedCrash):
        execute(wf.id)
    runner.fault = None

    # seq_first was recorded; seq_second's side effect ran but its record
    # did not land.
    assert CALLS.count("seq_first") == 1
    assert CALLS.count("seq_second") == 1
    recorded = set(
        Step.objects.filter(workflow_id=wf.id).values_list("step_id", flat=True)
    )
    assert "1.0.1" in recorded
    assert "1.0.2" not in recorded

    re_claim(wf)
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.COMPLETED
    assert wf.result == ["f", "s", "2"]
    # seq_first served from the store; seq_second re-ran.
    assert CALLS.count("seq_first") == 1
    assert CALLS.count("seq_second") == 2
    assert Step.objects.filter(workflow_id=wf.id).count() == 3


@workflow
def named_seq(args):
    parallel(
        [lambda: seq_first(everystep_id="first"), lambda: seq_second(everystep_id="second")],
        everystep_id="seq",
    )
    return None


def test_everystep_id_inside_sequence_branch():
    wf = run_to_completion(named_seq, {})
    assert {s.step_id for s in Step.objects.filter(workflow_id=wf.id)} == {
        "seq.0.first",
        "seq.0.second",
    }


@workflow
def named_seq_dup(args):
    parallel(
        [lambda: seq_first(everystep_id="dup"), lambda: seq_second(everystep_id="dup")],
        everystep_id="seq",
    )
    return "unreached"


def test_duplicate_everystep_id_in_sequence_fails_loudly():
    wf = schedule(named_seq_dup, {})
    claim_next()
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.FAILED
    assert wf.error["type"].endswith("EverystepError")
    assert "already used in this scope" in wf.error["message"]
    # The second call was rejected before its side effect ran.
    assert CALLS == ["seq_first"]


def test_empty_sequence_branch_rejected():
    with pytest.raises(ValueError):
        parallel([])


def test_sequence_element_must_be_callable():
    with pytest.raises(TypeError):
        parallel([1])


def test_sequence_branch_outside_workflow():
    assert parallel([seq_first, seq_second]) == (("f", "s"),)
    assert CALLS == ["seq_first", "seq_second"]
    assert Workflow.objects.count() == 0
    assert Step.objects.count() == 0
