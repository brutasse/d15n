import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import StatusCode

import everystep.runner as runner
import everystep.traces as traces
from everystep import parallel, schedule, step, workflow
from everystep.errors import SimulatedCrash, Terminal
from everystep.models import Workflow
from everystep.runner import execute
from everystep.worker import claim_new
from tests.helpers import crash_on, re_claim, run_to_completion

# transaction=True: parallel branches run on pool threads with their own
# database connections, which cannot see a test's uncommitted transaction.
pytestmark = pytest.mark.django_db(transaction=True)


class _InMemorySpanExporter(SpanExporter):
    """Collects finished spans in memory."""

    def __init__(self):
        self.finished_spans = []

    def export(self, spans):
        self.finished_spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True

    def get_finished_spans(self):
        return list(self.finished_spans)


@pytest.fixture(scope="module")
def _exporter():
    # The global tracer provider is set once per process.
    if isinstance(otel_trace.get_tracer_provider(), otel_trace.ProxyTracerProvider):
        otel_trace.set_tracer_provider(TracerProvider())
    exporter = _InMemorySpanExporter()
    otel_trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


@pytest.fixture
def spans(_exporter):
    _exporter.finished_spans.clear()
    yield _exporter


def _roots(spans):
    return [s for s in spans if s.parent is None]


def _children(spans):
    return [s for s in spans if s.parent is not None]


@step
def ok_step():
    return "ok"


@step
def other_step():
    return "other"


@step
def bad_step():
    raise ValueError("boom")


@step
def stop_step():
    raise Terminal("known-reason", {"k": "v"})


@workflow
def good(args):
    return ok_step()


@workflow
def bad(args):
    return bad_step()


@workflow
def stopped_wf(args):
    return stop_step()


@workflow
def two_steps(args):
    ok_step()
    other_step()
    return "done"


@workflow
def fanout(args):
    return parallel(
        lambda: ok_step(),
        lambda: other_step(),
        everystep_id="fan",
    )


def test_completed_run_has_run_and_step_spans(spans):
    run = run_to_completion(good, {})
    assert run.status == Workflow.Status.COMPLETED

    finished = spans.get_finished_spans()
    assert len(_roots(finished)) == 1
    root = _roots(finished)[0]
    assert root.name == "tests.test_traces.good"
    assert root.attributes["everystep.workflow.id"] == str(run.id)
    assert root.attributes["everystep.workflow.status"] == "completed"
    assert root.status.status_code == StatusCode.OK

    steps = _children(finished)
    assert len(steps) == 1
    step_span = steps[0]
    assert step_span.name == "tests.test_traces.ok_step"
    assert step_span.attributes["everystep.step.id"] == "1"
    assert step_span.status.status_code == StatusCode.OK
    assert step_span.parent.span_id == root.context.span_id
    assert step_span.context.trace_id == root.context.trace_id


def test_failed_run_marks_errors(spans):
    run = run_to_completion(bad, {})
    assert run.status == Workflow.Status.FAILED

    finished = spans.get_finished_spans()
    root = _roots(finished)[0]
    assert root.attributes["everystep.workflow.status"] == "failed"
    assert root.status.status_code == StatusCode.ERROR
    assert any(e.name == "exception" for e in root.events)

    step_span = _children(finished)[0]
    assert step_span.status.status_code == StatusCode.ERROR
    assert any(e.name == "exception" for e in step_span.events)


def test_stopped_run_is_not_an_error(spans):
    run = run_to_completion(stopped_wf, {})
    assert run.status == Workflow.Status.STOPPED

    finished = spans.get_finished_spans()
    root = _roots(finished)[0]
    assert root.attributes["everystep.workflow.status"] == "stopped"
    assert root.status.status_code == StatusCode.OK

    # The step that raised Terminal is still marked as a failed step.
    assert _children(finished)[0].status.status_code == StatusCode.ERROR


def test_replayed_step_is_not_respanned(spans):
    run = schedule(two_steps, {})
    claimed = claim_new(1, "crash-w")
    assert [w.id for w in claimed] == [run.id]
    runner.fault = crash_on("2")
    with pytest.raises(SimulatedCrash):
        execute(run.id)

    finished = spans.get_finished_spans()
    assert len(_roots(finished)) == 1
    assert _roots(finished)[0].attributes["everystep.workflow.status"] == "running"
    # Step 1 ran and was recorded; step 2 ran but the crash came before its
    # record, so both steps have a span from this claim.
    assert len(_children(finished)) == 2

    runner.fault = None
    re_claim(run)
    execute(run.id)
    run.refresh_from_db()
    assert run.status == Workflow.Status.COMPLETED

    finished = spans.get_finished_spans()
    assert len(_roots(finished)) == 2
    assert _roots(finished)[-1].attributes["everystep.workflow.status"] == "completed"
    # On the second claim step 1 is served from the store and not re-traced;
    # only step 2 re-executes.
    assert len(_children(finished)) == 3


def test_parallel_steps_nest_under_the_fork_span(spans):
    run = run_to_completion(fanout, {})
    assert run.status == Workflow.Status.COMPLETED

    finished = spans.get_finished_spans()
    root = _roots(finished)[0]
    forks = [s for s in finished if s.name == "parallel"]
    assert len(forks) == 1
    fork = forks[0]
    assert fork.attributes["everystep.parallel.id"] == "fan"
    assert fork.parent.span_id == root.context.span_id

    branch_steps = [s for s in _children(finished) if s.parent.span_id == fork.context.span_id]
    assert len(branch_steps) == 2
    assert {s.attributes["everystep.step.id"] for s in branch_steps} == {"fan.0.1", "fan.1.1"}


def test_step_outside_workflow_has_no_spans(spans):
    assert ok_step() == "ok"
    assert spans.get_finished_spans() == []


def test_parallel_outside_workflow_has_no_spans(spans):
    assert parallel(lambda: ok_step()) == ("ok",)
    assert spans.get_finished_spans() == []


def test_no_spans_when_disabled(spans, monkeypatch):
    monkeypatch.setattr(traces, "enabled", False)
    run = run_to_completion(good, {})
    assert run.status == Workflow.Status.COMPLETED
    assert spans.get_finished_spans() == []
