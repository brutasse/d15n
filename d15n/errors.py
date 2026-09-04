class D15nError(Exception):
    """Base class for d15n errors."""


class SimulatedCrash(D15nError):
    """Raised from a test fault handler to simulate a process death mid-step.

    The runner re-raises it without writing any further state, leaving the
    workflow exactly as it would be if the worker had died.
    """


class StepFailure(D15nError):
    """A recorded step failure whose original exception type is unavailable."""


class WorkflowCodeError(D15nError):
    """A workflow body diverged from its previously recorded step identities."""


class DrainOrphan(D15nError):
    """Raised at a step boundary when the runner is draining after a stop signal.

    The step in flight at the signal finishes and is recorded; no new step
    starts. The workflow is left running to be resumed by the next runner
    with the same name.
    """
