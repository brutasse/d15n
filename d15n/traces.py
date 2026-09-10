"""OpenTelemetry traces for workflow runs.

Requires the optional `otel` extra (pip install "d15n[otel]"). Without
opentelemetry-api, every function in this module is a no-op.

d15n only creates spans on the global tracer `d15n`; the host application
owns the TracerProvider and its exporters, exactly as it owns Sentry.
"""

import contextlib

try:
    from opentelemetry import context as otel_context
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import Status, StatusCode
except ImportError:
    otel_context = None
    otel_trace = None
    Status = None
    StatusCode = None

enabled = otel_trace is not None

_tracer = otel_trace.get_tracer("d15n") if enabled else None


class _NoopSpan:
    """Absorbs span calls made while tracing is off."""

    def set_attribute(self, *args):
        pass

    def record_exception(self, *args, **kwargs):
        pass

    def set_status(self, *args, **kwargs):
        pass


@contextlib.contextmanager
def span(name, attributes=None, active=True):
    """Start a span as the current span, so nested spans become its children.

    Yields a no-op span when the otel extra is absent or `active` is False.
    Exception handling is d15n's: status is set explicitly via mark_error
    and mark_ok, never inferred from an unwound span.
    """
    if not enabled or not active:
        yield _NoopSpan()
        return
    with _tracer.start_as_current_span(
        name,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as s:
        yield s


def mark_error(span, error):
    if not enabled:
        return
    span.record_exception(error)
    span.set_status(Status(StatusCode.ERROR))


def mark_ok(span):
    if not enabled:
        return
    span.set_status(Status(StatusCode.OK))


def capture_context():
    """The OTel context to carry into a worker thread, or None."""
    if not enabled:
        return None
    return otel_context.get_current()


@contextlib.contextmanager
def attach_context(otel_ctx):
    """Attach a context captured in another thread. No-op for None."""
    if otel_ctx is None:
        yield
        return
    token = otel_context.attach(otel_ctx)
    try:
        yield
    finally:
        otel_context.detach(token)
