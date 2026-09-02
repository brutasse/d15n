import itertools
import threading

from d15n.errors import D15nError

_local = threading.local()

_MAX_ID_LENGTH = 300


def _validate_name(name):
    if not isinstance(name, str) or not name:
        raise D15nError("d15n_id must be a non-empty string")
    if "." in name:
        raise D15nError(f"d15n_id {name!r} must not contain dots (dots separate scopes)")
    if any(c.isspace() for c in name):
        raise D15nError(f"d15n_id {name!r} must not contain whitespace")
    if name.isdigit():
        raise D15nError(f"d15n_id {name!r} is purely numeric; names must not look like positions")


class Context:
    """Execution state for one workflow run, or one branch of one fork.

    Steps are identified by a dotpath like "3" or "3.1.2". Unnamed steps
    take the next position in their scope; a step called with `d15n_id`
    takes that name as its segment instead, which makes its id stable
    against edits elsewhere in the body. Names must be unique within a
    scope (one body, or one branch). The shared `outcomes` dict maps step
    ids to `{name, status, result, error}` outcomes. It is seeded with all
    outcomes recorded before the current claim and updated as each step
    completes, so inside a step you can read any previously-completed
    step's outcome from the current context. Treat it as read-only.
    """

    def __init__(self, workflow_id, outcomes, persistent, prefix=""):
        self.workflow_id = workflow_id
        self.outcomes = outcomes
        self.persistent = persistent
        self.prefix = prefix
        self.counter = itertools.count(1)
        self._used_ids = set()

    def next_id(self, name=None):
        # Always consume a position, so naming (or un-naming) a step never
        # shifts the ids of the other unnamed steps in the scope.
        tick = next(self.counter)
        if name is None:
            segment = str(tick)
        else:
            _validate_name(name)
            segment = name
        step_id = f"{self.prefix}{segment}"
        if len(step_id) > _MAX_ID_LENGTH:
            raise D15nError(f"d15n_id {name!r} is too long: step id would exceed {_MAX_ID_LENGTH} characters")
        if step_id in self._used_ids:
            raise D15nError(
                f"d15n_id {segment!r} is already used in this scope (step id "
                f"{step_id!r}); d15n_ids must be unique per body or branch"
            )
        self._used_ids.add(step_id)
        return step_id

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
