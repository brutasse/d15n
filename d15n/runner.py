"""Replay-based execution engine.

A claimed workflow's body is re-run from the top on every claim. Step calls
whose outcome is already recorded are served from the Step table; execution
resumes at the first unrecorded step.

`fault` is a test hook: when set, it is called (ctx, step_id) after a step's
side effect has run but before its outcome is recorded. Raising
SimulatedCrash simulates a worker process dying at that point.
"""

import time

from django.db import transaction
from django.utils import timezone

from d15n import context, metrics, serde
from d15n.context import Context
from d15n.errors import (
    D15nError,
    DrainOrphan,
    SimulatedCrash,
    Terminal,
    WorkflowCodeError,
)
from d15n.models import Step, Workflow
from d15n.registry import name_of, registry
from d15n.telemetry import report_workflow_failure

fault = None


def run_step(ctx, func, args, kwargs, d15n_id=None):
    name = name_of(func)
    step_id = ctx.next_id(d15n_id)
    stored = ctx.outcomes.get(step_id)
    if stored is not None:
        if stored["name"] != name:
            raise WorkflowCodeError(
                f"workflow code changed at step {step_id!r}: previously "
                f"{stored['name']!r}, now {name!r} "
                f"(workflow {ctx.workflow_id}). Workflow bodies must be "
                "deterministic across runs."
            )
        if stored["status"] == Step.Status.FAILED:
            raise serde.decode_exception(stored["error"])
        return stored["result"]

    if ctx.draining is not None and ctx.draining.is_set():
        raise DrainOrphan()

    try:
        serde.dumps(list(args))
        serde.dumps(kwargs)
    except (TypeError, ValueError) as exc:
        raise D15nError(f"step {name!r} arguments are not serializable: {exc}") from exc

    started = time.monotonic()
    try:
        result = func(*args, **kwargs)
        error = None
    except Exception as exc:
        result = None
        error = exc
    duration = time.monotonic() - started

    if error is None:
        try:
            serde.dumps(result)
        except (TypeError, ValueError) as exc:
            raise D15nError(f"step {name!r} result is not serializable: {exc}") from exc

    if ctx.persistent:
        if fault is not None:
            fault(ctx, step_id)
        status = Step.Status.FAILED if error is not None else Step.Status.DONE
        error_payload = serde.encode_exception(error) if error is not None else None
        with transaction.atomic():
            Step.objects.create(
                workflow_id=ctx.workflow_id,
                step_id=step_id,
                name=name,
                args=list(args),
                kwargs=kwargs,
                status=status,
                result=result,
                error=error_payload,
            )
        metrics.record_step(ctx.workflow_name, name, status, duration)
        # Keep the shared outcome store current, so a later step can read
        # this step's outcome via context.current().outcomes. On a replay the
        # outcome is already present from the claim-time snapshot.
        ctx.outcomes[step_id] = {
            "name": name,
            "status": status,
            "result": result,
            "error": error_payload,
        }

    if error is not None:
        raise error
    return result


def execute(workflow_id, draining=None):
    """Run a claimed workflow to a terminal state (or a simulated crash).

    `draining` is an Event the runner sets on stop: the step in flight at
    the signal finishes and is recorded, but no new step starts; the
    DrainOrphan raised at the next step boundary is caught here and the
    workflow is left running for the next runner to resume.
    """
    started = time.monotonic()
    workflow = Workflow.objects.get(id=workflow_id)
    func = registry.resolve(workflow.name)

    outcomes = {
        row.step_id: {
            "name": row.name,
            "status": row.status,
            "result": row.result,
            "error": row.error,
        }
        for row in Step.objects.filter(workflow_id=workflow.id)
    }

    ctx = Context(
        workflow_id=workflow.id,
        outcomes=outcomes,
        persistent=True,
        draining=draining,
        workflow_name=workflow.name,
    )
    context.set_current(ctx)
    try:
        try:
            result = func(*workflow.args)
            error = None
        except SimulatedCrash:
            raise
        except DrainOrphan:
            return
        except Terminal as t:
            updated = Workflow.objects.filter(id=workflow.id, status=Workflow.Status.RUNNING).update(
                status=Workflow.Status.STOPPED,
                error=serde.encode_exception(t),
                completed_at=timezone.now(),
            )
            if updated:
                metrics.record_workflow_terminal(workflow.name, "stopped", time.monotonic() - started)
            return
        except Exception as exc:
            result = None
            error = exc
    finally:
        context.clear_current()

    now = timezone.now()
    if error is not None:
        report_workflow_failure(error, workflow_id=workflow.id, workflow_name=workflow.name)
        updated = Workflow.objects.filter(id=workflow.id, status=Workflow.Status.RUNNING).update(
            status=Workflow.Status.FAILED,
            error=serde.encode_exception(error),
            completed_at=now,
        )
        if updated:
            metrics.record_workflow_terminal(workflow.name, "failed", time.monotonic() - started)
        return
    try:
        serde.dumps(result)
    except (TypeError, ValueError) as exc:
        raise D15nError(f"workflow result for {workflow.name!r} is not serializable: {exc}") from exc
    updated = Workflow.objects.filter(id=workflow.id, status=Workflow.Status.RUNNING).update(
        status=Workflow.Status.COMPLETED,
        result=result,
        completed_at=now,
    )
    if updated:
        metrics.record_workflow_terminal(workflow.name, "completed", time.monotonic() - started)
