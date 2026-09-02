import pytest

from d15n import context, parallel, runner
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
def s_one():
    CALLS.append("s_one")
    return "1"


@step
def s_two():
    CALLS.append("s_two")
    return "2"


@step
def s_three(value):
    CALLS.append("s_three")
    return f"{value}!"


@step
def s_int(n):
    CALLS.append("s_int")
    return n


@workflow
def named_chain(args):
    return s_three(s_one(d15n_id="one") + s_two())


def test_named_step_id_with_numeric_siblings():
    wf = run_to_completion(named_chain, {})
    assert wf.status == Workflow.Status.COMPLETED
    assert wf.result == "12!"
    rows = {s.step_id: s.name for s in Step.objects.filter(workflow_id=wf.id)}
    assert rows == {
        "one": f"{__name__}.s_one",
        "2": f"{__name__}.s_two",
        "3": f"{__name__}.s_three",
    }


@workflow
def unnamed_chain(args):
    return s_one() + s_two() + s_three("x")


@workflow
def half_named_chain(args):
    return s_one() + s_two(d15n_id="mid") + s_three("x")


def test_naming_a_step_does_not_shift_other_ids():
    wf1 = run_to_completion(unnamed_chain, {})
    assert set(
        Step.objects.filter(workflow_id=wf1.id).values_list("step_id", flat=True)
    ) == {"1", "2", "3"}
    wf2 = run_to_completion(half_named_chain, {})
    assert {s.step_id for s in Step.objects.filter(workflow_id=wf2.id)} == {
        "1",
        "mid",
        "3",
    }


@workflow
def two_named(args):
    return s_one(d15n_id="first") + s_two(d15n_id="second")


@workflow
def two_named_plus(args):
    return (
        s_one(d15n_id="first")
        + s_three("x", d15n_id="inserted")
        + s_two(d15n_id="second")
    )


def test_named_steps_stable_under_insertion():
    wf_a = run_to_completion(two_named, {})
    assert {s.step_id for s in Step.objects.filter(workflow_id=wf_a.id)} == {
        "first",
        "second",
    }
    wf_b = run_to_completion(two_named_plus, {})
    assert {s.step_id for s in Step.objects.filter(workflow_id=wf_b.id)} == {
        "first",
        "inserted",
        "second",
    }


@workflow
def named_fork(args):
    s_one()
    a, b = parallel(
        lambda: s_two(d15n_id="left"),
        lambda: s_three("z", d15n_id="right"),
        d15n_id="fanout",
    )
    return a + b


def test_named_fork_and_branch_steps():
    wf = run_to_completion(named_fork, {})
    assert wf.result == "2z!"
    rows = {s.step_id: s.name for s in Step.objects.filter(workflow_id=wf.id)}
    assert rows == {
        "1": f"{__name__}.s_one",
        "fanout.0.left": f"{__name__}.s_two",
        "fanout.1.right": f"{__name__}.s_three",
    }


@workflow
def duplicate_name(args):
    s_one(d15n_id="x")
    s_two(d15n_id="x")
    return "unreached"


def test_duplicate_d15n_id_in_scope_fails_loudly():
    wf = schedule(duplicate_name, {})
    claim_next()
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.FAILED
    assert wf.error["type"].endswith("D15nError")
    assert "already used in this scope" in wf.error["message"]
    # The second call was rejected before its side effect ran.
    assert CALLS == ["s_one"]


@workflow
def same_name_in_branches(args):
    a, b = parallel(
        lambda: s_one(d15n_id="x"),
        lambda: s_two(d15n_id="x"),
    )
    return a + b


def test_same_d15n_id_ok_in_different_branches():
    wf = run_to_completion(same_name_in_branches, {})
    assert wf.result == "12"
    assert {s.step_id for s in Step.objects.filter(workflow_id=wf.id)} == {
        "1.0.x",
        "1.1.x",
    }


@workflow
def fork_name_collides(args):
    s_one(d15n_id="fanout")
    parallel(lambda: s_two(), d15n_id="fanout")
    return "unreached"


def test_fork_name_colliding_with_step_name_fails_loudly():
    wf = schedule(fork_name_collides, {})
    claim_next()
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.FAILED
    assert wf.error["type"].endswith("D15nError")
    assert "already used in this scope" in wf.error["message"]
    assert CALLS == ["s_one"]


@workflow
def named_by_arg(args):
    return s_one(d15n_id=args["id"])


@pytest.mark.parametrize("bad", ["", "a.b", "123", "a b", 42])
def test_invalid_d15n_id_fails_loudly(bad):
    wf = schedule(named_by_arg, {"id": bad})
    claim_next()
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.FAILED
    assert wf.error["type"].endswith("D15nError")
    assert wf.error["message"].startswith("d15n_id")
    assert CALLS == []
    assert Step.objects.filter(workflow_id=wf.id).count() == 0


@workflow
def named_flaky(args):
    return s_one(d15n_id="first") + s_two(d15n_id="second") + s_three("k")


def test_named_step_crash_and_resume():
    wf = schedule(named_flaky, {})
    claim_next()

    runner.fault = crash_on("second")
    with pytest.raises(SimulatedCrash):
        execute(wf.id)
    runner.fault = None

    # "first" was recorded; "second"'s side effect ran but its record did not
    # land.
    assert CALLS == ["s_one", "s_two"]
    assert set(
        Step.objects.filter(workflow_id=wf.id).values_list("step_id", flat=True)
    ) == {"first"}

    re_claim(wf)
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.COMPLETED
    assert wf.result == "12k!"
    assert CALLS == ["s_one", "s_two", "s_two", "s_three"]
    assert Step.objects.filter(workflow_id=wf.id).count() == 3


@step
def source():
    CALLS.append("source")
    return "S"


@step
def read_named(which):
    CALLS.append("read_named")
    return f"{which}={context.current().outcomes[which]['result']}"


@workflow
def reads_previous(args):
    source(d15n_id="source")
    return read_named("source")


def test_step_reads_previous_result_fresh():
    wf = run_to_completion(reads_previous, {})
    assert wf.status == Workflow.Status.COMPLETED
    assert wf.result == "source=S"
    assert CALLS == ["source", "read_named"]
    assert {s.step_id for s in Step.objects.filter(workflow_id=wf.id)} == {
        "source",
        "2",
    }


def test_step_reads_previous_result_on_replay():
    wf = schedule(reads_previous, {})
    claim_next()

    runner.fault = crash_on("2")
    with pytest.raises(SimulatedCrash):
        execute(wf.id)
    runner.fault = None

    # read_named's side effect ran but its record did not land.
    assert CALLS == ["source", "read_named"]
    assert set(
        Step.objects.filter(workflow_id=wf.id).values_list("step_id", flat=True)
    ) == {"source"}

    re_claim(wf)
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.COMPLETED
    assert wf.result == "source=S"
    # "source" served from the store (not re-run); read_named re-ran and
    # re-read the stored result.
    assert CALLS == ["source", "read_named", "read_named"]


SLOT = {"fn": "a"}


@workflow
def named_slot(args):
    if SLOT["fn"] == "a":
        s_one(d15n_id="slot")
    else:
        s_two(d15n_id="slot")
    s_three("k")
    return "done"


def test_code_change_at_named_slot_detected():
    wf = schedule(named_slot, {})
    claim_next()

    runner.fault = crash_on("2")
    with pytest.raises(SimulatedCrash):
        execute(wf.id)
    runner.fault = None

    assert set(
        Step.objects.filter(workflow_id=wf.id).values_list("step_id", flat=True)
    ) == {"slot"}

    SLOT["fn"] = "b"
    re_claim(wf)
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.FAILED
    assert wf.error["type"].endswith("WorkflowCodeError")
    assert "s_one" in wf.error["message"]
    assert "s_two" in wf.error["message"]


@workflow
def looped(args):
    return sum(s_int(i, d15n_id=f"iter-{i}") for i in range(2))


def test_dynamic_names_in_a_loop():
    wf = run_to_completion(looped, {})
    assert wf.result == 1
    assert {s.step_id for s in Step.objects.filter(workflow_id=wf.id)} == {
        "iter-0",
        "iter-1",
    }


def test_d15n_id_ignored_outside_workflow():
    assert s_one(d15n_id="x") == "1"
    assert s_three("v", d15n_id="y") == "v!"
    assert CALLS == ["s_one", "s_three"]
    assert Workflow.objects.count() == 0
    assert Step.objects.count() == 0
