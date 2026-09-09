import pytest

from d15n import Terminal, parallel, schedule, step, workflow
from d15n import serde
from d15n.models import Step, Workflow
from d15n.runner import execute
from tests.helpers import claim_next

pytestmark = pytest.mark.django_db(transaction=True)

CALLS = []


@pytest.fixture(autouse=True)
def _calls():
    CALLS.clear()
    yield


@step
def upload(node_id):
    CALLS.append(f"upload:{node_id}")
    raise Terminal("node-full", {"node_id": node_id})


@step
def after():
    CALLS.append("after")
    return "done"


@workflow
def upload_flow(args):
    upload(args["node"])
    after()
    return "ok"


def test_terminal_stops_workflow():
    wf = schedule(upload_flow, {"node": "a"})
    claim_next()
    execute(wf.id)
    wf.refresh_from_db()
    assert wf.status == Workflow.Status.STOPPED
    assert wf.completed_at is not None
    # The in-flight step ran and was recorded; the later step never started.
    assert CALLS == ["upload:a"]
    step_row = Step.objects.get(workflow_id=wf.id)
    assert step_row.step_id == "1"
    assert step_row.status == Step.Status.FAILED
    # The caller reads the reason and payload back from the workflow's error.
    t = serde.decode_exception(wf.error)
    assert isinstance(t, Terminal)
    assert t.reason == "node-full"
    assert t.payload == {"node_id": "a"}


@step
def ok_branch():
    CALLS.append("ok_branch")
    return "ok"


@step
def terminal_branch():
    CALLS.append("terminal_branch")
    raise Terminal("stop", {"x": 1})


@workflow
def parallel_terminal_flow(args):
    a, b = parallel(lambda: ok_branch(), lambda: terminal_branch())
    return [a, b]


def test_terminal_in_parallel_stops_workflow():
    wf = schedule(parallel_terminal_flow, {})
    claim_next()
    execute(wf.id)
    wf.refresh_from_db()
    assert wf.status == Workflow.Status.STOPPED
    t = serde.decode_exception(wf.error)
    assert isinstance(t, Terminal)
    assert t.reason == "stop"
    assert t.payload == {"x": 1}
