"""d15n: durable workflow execution for Django."""

from d15n.errors import Terminal

__all__ = ["Terminal", "parallel", "schedule", "step", "workflow"]


def __getattr__(name):
    if name in __all__:
        from d15n import api

        return getattr(api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
