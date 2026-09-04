"""Public client API: @workflow, @step, parallel(), schedule()."""

import functools
from concurrent.futures import ThreadPoolExecutor

from django.db import connections

from d15n import context, serde
from d15n.context import Context
from d15n.errors import D15nError, DrainOrphan
from d15n.models import Workflow
from d15n.registry import name_of, registry
from d15n.runner import run_step

_WORKFLOW = "workflow"
_STEP = "step"


def workflow(func):
    if not callable(func):
        raise TypeError("@workflow must decorate a function")

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    wrapper.__d15n__ = _WORKFLOW
    return registry.register_workflow(wrapper)


def step(func):
    if not callable(func):
        raise TypeError("@step must decorate a function")

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        d15n_id = kwargs.pop("d15n_id", None)
        ctx = context.current()
        if ctx is None:
            return func(*args, **kwargs)
        return run_step(ctx, func, args, kwargs, d15n_id)

    wrapper.__d15n__ = _STEP
    return registry.register_step(wrapper)


def schedule(workflow_func, *args, idempotency_key=None):
    """Insert a scheduled workflow into the current transaction.

    The workflow becomes claimable by workers once the transaction commits.
    With an idempotency_key, a concurrent or repeated schedule with the same
    key returns the existing workflow instead of creating a new one.
    """
    if getattr(workflow_func, "__d15n__", None) != _WORKFLOW:
        raise TypeError(f"{workflow_func!r} is not a @workflow-decorated function")
    try:
        serde.dumps(list(args))
    except (TypeError, ValueError) as exc:
        raise D15nError(f"workflow arguments are not serializable: {exc}") from exc

    defaults = {"args": list(args), "status": Workflow.Status.SCHEDULED}
    if idempotency_key is None:
        return Workflow.objects.create(name=name_of(workflow_func), **defaults)
    run, _created = Workflow.objects.get_or_create(
        name=name_of(workflow_func), idempotency_key=idempotency_key, defaults=defaults
    )
    return run


class _Finished:
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class _Failed:
    __slots__ = ("exc",)

    def __init__(self, exc):
        self.exc = exc


def _run_branch(ctx, fork_id, index, branch):
    context.set_current(ctx.branch(fork_id, index))
    try:
        try:
            return _Finished(branch())
        except Exception as exc:
            return _Failed(exc)
    finally:
        context.clear_current()
        # Branches run on pool threads and Django connections are thread-local;
        # release this thread's connection so it is not leaked when the
        # pool thread exits.
        connections.close_all()


def parallel(*branches, d15n_id=None):
    """Run zero-arg callables concurrently; return their results in order.

    Each branch is a single step (a @step function or a lambda calling one).
    If any branch raises, the first error is re-raised (single branch) or an
    ExceptionGroup is raised (several branches).

    Pass d15n_id="..." to give the fork a stable name, so the branch step
    ids ("name.0.1", "name.1.1") do not shift when steps before it change.
    """
    if not branches:
        raise ValueError("parallel() requires at least one branch")

    ctx = context.current()
    owns_ctx = False
    if ctx is None:
        ctx = Context(workflow_id=None, outcomes={}, persistent=False)
        context.set_current(ctx)
        owns_ctx = True
    try:
        fork_id = ctx.next_id(d15n_id)
        with ThreadPoolExecutor(
            max_workers=len(branches), thread_name_prefix="d15n-parallel"
        ) as pool:
            futures = [
                pool.submit(_run_branch, ctx, fork_id, index, branch)
                for index, branch in enumerate(branches)
            ]
            outputs = [future.result() for future in futures]
    finally:
        if owns_ctx:
            context.clear_current()

    errors = [output for output in outputs if isinstance(output, _Failed)]
    if not errors:
        return tuple(output.value for output in outputs)
    drains = [e for e in errors if isinstance(e.exc, DrainOrphan)]
    if drains:
        raise drains[0].exc
    if len(errors) == 1:
        raise errors[0].exc
    raise ExceptionGroup("d15n parallel branches failed", [e.exc for e in errors])
