"""Sentry reporting for unhandled workflow failures.

If sentry-sdk is installed and initialized in the host application, every
unhandled exception that fails a workflow run is reported to Sentry with the
workflow's identity attached. Without sentry-sdk, reporting is a no-op.
"""

from d15n.errors import DrainOrphan, SimulatedCrash


def report_workflow_failure(exc, *, workflow_id, workflow_name=None):
    """Report an unhandled workflow failure to Sentry, if configured.

    Control-flow exceptions (SimulatedCrash, DrainOrphan) are not failures
    and are never reported.
    """
    if isinstance(exc, (SimulatedCrash, DrainOrphan)):
        return
    try:
        import sentry_sdk
    except ImportError:
        return
    with sentry_sdk.isolation_scope() as scope:
        d15n_context = {"workflow_id": str(workflow_id)}
        if workflow_name is not None:
            d15n_context["workflow"] = workflow_name
        scope.set_context("d15n", d15n_context)
        sentry_sdk.capture_exception(exc)
