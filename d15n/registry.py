import importlib


def name_of(func):
    return f"{func.__module__}.{func.__qualname__}"


def import_dotted(path):
    """Best-effort import of a class by dotted name. Returns None if unavailable."""
    module_name, sep, qualname = path.rpartition(".")
    if not sep:
        return None
    try:
        obj = importlib.import_module(module_name)
    except ImportError:
        return None
    for part in qualname.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            return None
    return obj


class Registry:
    def __init__(self):
        self._workflows = {}
        self._steps = {}

    def register_workflow(self, func):
        self._workflows[name_of(func)] = func
        return func

    def register_step(self, func):
        self._steps[name_of(func)] = func
        return func

    def resolve(self, name):
        func = self._workflows.get(name) or self._steps.get(name)
        if func is not None:
            return func
        obj = import_dotted(name)
        if obj is None:
            raise LookupError(f"d15n cannot resolve workflow function {name!r}")
        return obj


registry = Registry()
