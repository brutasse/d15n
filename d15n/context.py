import itertools
import threading

_local = threading.local()


class Context:
    """Execution state for one workflow run, or one branch of one fork.

    Steps are identified by the position they occupy in the body (a dotpath
    like "3" or "3.1.2"), not by explicit names. The shared `outcomes` dict
    maps step ids to recorded outcomes and is read-only during a run.
    """

    def __init__(self, workflow_id, outcomes, persistent, prefix=""):
        self.workflow_id = workflow_id
        self.outcomes = outcomes
        self.persistent = persistent
        self.prefix = prefix
        self.counter = itertools.count(1)

    def next_id(self):
        return f"{self.prefix}{next(self.counter)}"

    def branch(self, fork_id, index):
        return Context(
            workflow_id=self.workflow_id,
            outcomes=self.outcomes,
            persistent=self.persistent,
            prefix=f"{fork_id}.{index}.",
        )


def current():
    return getattr(_local, "ctx", None)


def set_current(ctx):
    _local.ctx = ctx


def clear_current():
    _local.ctx = None
