import enum
import uuid as uuid_lib
from datetime import datetime

import pytest

from d15n import runner
from d15n import schedule, step, workflow
from d15n.errors import SimulatedCrash
from d15n.models import Step, Workflow
from d15n.runner import execute
from tests.helpers import claim_next, crash_on, re_claim

pytestmark = pytest.mark.django_db

CALLS = []


@pytest.fixture(autouse=True)
def _calls():
    CALLS.clear()
    yield


@step
def one():
    CALLS.append("one")
    return "1"


@step
def two():
    CALLS.append("two")
    return "2"


@step
def three():
    CALLS.append("three")
    return "3"


@workflow
def flaky(args):
    return one() + two() + three()


def test_crash_before_record_and_resume():
    wf = schedule(flaky, {})
    claim_next()

    runner.fault = crash_on("2")
    with pytest.raises(SimulatedCrash):
        execute(wf.id)
    runner.fault = None

    # Step 1 was recorded; step 2's side effect ran but its record did not land.
    assert CALLS == ["one", "two"]
    assert set(Step.objects.filter(workflow_id=wf.id).values_list("step_id", flat=True)) == {"1"}
    wf.refresh_from_db()
    assert wf.status == Workflow.Status.RUNNING

    re_claim(wf)
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.COMPLETED
    assert wf.result == "123"
    # Step 1 served from the store, step 2 re-executed, step 3 executed.
    assert CALLS == ["one", "two", "two", "three"]
    assert Step.objects.filter(workflow_id=wf.id).count() == 3


class IpConflict(Exception):
    pass


@step
def acquire():
    CALLS.append("acquire")
    raise IpConflict("address in use")


@step
def release():
    CALLS.append("release")
    return None


@workflow
def guarded(args):
    try:
        acquire()
    except IpConflict:
        release()
        raise
    return "ok"


def test_failed_step_reraises_and_cleanup_replays():
    wf = schedule(guarded, {})
    claim_next()

    runner.fault = crash_on("2")
    with pytest.raises(SimulatedCrash):
        execute(wf.id)
    runner.fault = None

    assert CALLS == ["acquire", "release"]
    assert set(Step.objects.filter(workflow_id=wf.id).values_list("step_id", flat=True)) == {"1"}

    re_claim(wf)
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.FAILED
    assert wf.error["type"] == f"{IpConflict.__module__}.{IpConflict.__qualname__}"
    assert wf.error["message"] == "address in use"
    # The stored failure re-raised; cleanup ran once per pass (at-least-once).
    assert CALLS == ["acquire", "release", "release"]
    rows = {
        s.step_id: s.status
        for s in Step.objects.filter(workflow_id=wf.id)
    }
    assert rows == {"1": Step.Status.FAILED, "2": Step.Status.DONE}

    stored = Step.objects.get(workflow_id=wf.id, step_id="1")
    assert stored.error["type"] == f"{IpConflict.__module__}.{IpConflict.__qualname__}"
    assert stored.error["args"] == ["address in use"]


BRANCH = {"pick": "a"}


@step
def begin():
    return "started"


@step
def step_a():
    return "a"


@step
def step_b():
    return "b"


@step
def finish(x):
    return f"finished:{x}"


@workflow
def branchy(args):
    begin()
    x = step_a() if BRANCH["pick"] == "a" else step_b()
    return finish(x)


def test_code_change_detected_on_replay():
    wf = schedule(branchy, {})
    claim_next()

    runner.fault = crash_on("3")
    with pytest.raises(SimulatedCrash):
        execute(wf.id)
    runner.fault = None

    assert set(
        Step.objects.filter(workflow_id=wf.id).values_list("step_id", flat=True)
    ) == {"1", "2"}

    BRANCH["pick"] = "b"
    re_claim(wf)
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.FAILED
    assert wf.error["type"].endswith("WorkflowCodeError")
    assert "step_a" in wf.error["message"]
    assert "step_b" in wf.error["message"]


class Level(enum.Enum):
    LOW = 1
    HIGH = 2


@step
def pack(args):
    return {"uid": args["uid"], "when": args["when"], "level": args["level"]}


@step
def unpack(value):
    return value


@workflow
def carrier(args):
    return unpack(pack(args))


def test_typed_values_roundtrip_through_store():
    uid = uuid_lib.uuid4()
    when = datetime(2026, 1, 2, 3, 4, 5, 123456)
    wf = schedule(carrier, {"uid": uid, "when": when, "level": Level.HIGH})
    claim_next()

    runner.fault = crash_on("2")
    with pytest.raises(SimulatedCrash):
        execute(wf.id)
    runner.fault = None

    re_claim(wf)
    execute(wf.id)
    wf.refresh_from_db()

    assert wf.status == Workflow.Status.COMPLETED
    assert isinstance(wf.result["uid"], uuid_lib.UUID) and wf.result["uid"] == uid
    assert isinstance(wf.result["when"], datetime) and wf.result["when"] == when
    assert wf.result["level"] is Level.HIGH

    packed = Step.objects.get(workflow_id=wf.id, step_id="1")
    assert packed.kwargs == {}
    assert packed.args[0]["uid"] == uid
    assert isinstance(packed.args[0]["uid"], uuid_lib.UUID)
    assert packed.args[0]["level"] is Level.HIGH
