"""everystep: durable workflow execution for Django."""

from everystep.errors import Terminal

__all__ = ["Terminal", "parallel", "schedule", "step", "workflow"]


def __getattr__(name):
    if name in __all__:
        from everystep import api

        return getattr(api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
