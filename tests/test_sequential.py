import pytest

from django.db import transaction

from d15n import parallel, schedule, step, workflow
from d15n.models import Step, Workflow
from tests.helpers import run_to_completion

pytestmark = pytest.mark.django_db

CALLS = []


@pytest.fixture(autouse=True)
def _calls():
    CALLS.clear()
    yield


@step
def alpha():
    CALLS.append("alpha")
    return "A"


@step
def beta():
    CALLS.append("beta")
    return "B"


@step
def gamma(value):
    CALLS.append("gamma")
    return f"{value}!"


@workflow
def chain(args):
    return gamma(alpha() + beta())


def test_sequential_run_to_completion():
    wf = run_to_completion(chain, {"n": 7})
    assert wf.status == Workflow.Status.COMPLETED
    assert wf.result == "AB!"
    assert wf.args == [{"n": 7}]

    rows = list(Step.objects.filter(workflow_id=wf.id).order_by("step_id"))
    assert [s.step_id for s in rows] == ["1", "2", "3"]
    assert [s.name for s in rows] == [
        f"{__name__}.alpha",
        f"{__name__}.beta",
        f"{__name__}.gamma",
    ]
    assert [s.status for s in rows] == [Step.Status.DONE] * 3
    assert rows[2].args == ["AB"]
    assert rows[2].result == "AB!"
    assert CALLS == ["alpha", "beta", "gamma"]


def test_schedule_rolls_back_with_caller_transaction():
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            schedule(chain, {"n": 1})
            raise RuntimeError("rollback")
    assert Workflow.objects.count() == 0


def test_schedule_commits_with_caller_transaction():
    with transaction.atomic():
        schedule(chain, {"n": 1})
    assert Workflow.objects.count() == 1


def test_idempotency_key_returns_existing_workflow():
    first = schedule(chain, {"n": 1}, idempotency_key="order-42")
    second = schedule(chain, {"n": 2}, idempotency_key="order-42")
    assert first.id == second.id
    assert Workflow.objects.count() == 1
    assert Workflow.objects.get(id=first.id).args == [{"n": 1}]


def test_schedule_requires_workflow():
    with pytest.raises(TypeError):
        schedule(alpha, 1)


def test_parallel_requires_a_branch():
    with pytest.raises(ValueError):
        parallel()


def test_direct_call_bypasses_persistence():
    assert chain({"n": 1}) == "AB!"
    assert alpha() == "A"
    assert CALLS == ["alpha", "beta", "gamma", "alpha"]
    assert Workflow.objects.count() == 0
    assert Step.objects.count() == 0
