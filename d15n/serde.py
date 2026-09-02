"""JSON (de)serialization for workflow data and recorded exceptions."""

import base64
import datetime
import enum
import json
import uuid

from d15n.errors import StepFailure
from d15n.registry import import_dotted

_TAG = "__d15n_type__"


def dumps(value):
    return json.dumps(value, default=_encode)


def loads(raw):
    return json.loads(raw, object_hook=_decode)


def json_default(obj):
    """json.JSONEncoder.default hook for d15n's extended types."""
    return _encode(obj)


def json_object_hook(obj):
    """json.JSONDecoder object_hook for d15n's extended types."""
    return _decode(obj)


def _encode(obj):
    if isinstance(obj, datetime.datetime):
        return {_TAG: "datetime", "value": obj.isoformat()}
    if isinstance(obj, datetime.date):
        return {_TAG: "date", "value": obj.isoformat()}
    if isinstance(obj, datetime.timedelta):
        return {_TAG: "timedelta", "value": obj.total_seconds()}
    if isinstance(obj, uuid.UUID):
        return {_TAG: "uuid", "value": str(obj)}
    if isinstance(obj, (bytes, bytearray)):
        return {_TAG: "bytes", "value": base64.b64encode(bytes(obj)).decode("ascii")}
    if isinstance(obj, enum.Enum):
        return {
            _TAG: "enum",
            "type": f"{type(obj).__module__}.{type(obj).__qualname__}",
            "value": obj.value,
        }
    raise TypeError(
        "d15n cannot serialize %s; workflow data must be JSON-serializable "
        "(datetime, date, timedelta, UUID, bytes and enum are supported)"
        % type(obj).__name__
    )


def _decode(obj):
    if set(obj) == {_TAG, "value"}:
        kind = obj[_TAG]
        value = obj["value"]
        if kind == "datetime":
            return datetime.datetime.fromisoformat(value)
        if kind == "date":
            return datetime.date.fromisoformat(value)
        if kind == "timedelta":
            return datetime.timedelta(seconds=value)
        if kind == "uuid":
            return uuid.UUID(value)
        if kind == "bytes":
            return base64.b64decode(value)
    if set(obj) == {_TAG, "type", "value"} and obj[_TAG] == "enum":
        cls = import_dotted(obj["type"])
        if cls is not None and isinstance(cls, type):
            try:
                return cls(obj["value"])
            except (ValueError, KeyError):
                pass
    return obj


def encode_exception(exc):
    payload = {
        "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
        "message": str(exc),
    }
    try:
        payload["args"] = json.loads(dumps(list(exc.args)))
    except TypeError:
        payload["args"] = []
    return payload


def decode_exception(payload):
    if not isinstance(payload, dict):
        return StepFailure(f"step failed (unrecognized failure record: {payload!r})")
    message = payload.get("message", "")
    type_path = payload.get("type", "")
    args = payload.get("args") or []
    exc_type = import_dotted(type_path) if type_path else None
    if exc_type is not None and isinstance(exc_type, type) and issubclass(exc_type, BaseException):
        attempts = [args] if args else ([message] if message else [])
        for attempt in attempts:
            try:
                return exc_type(*attempt)
            except Exception:
                continue
    if message:
        return StepFailure(f"{message} (original exception type {type_path!r} unavailable)")
    return StepFailure("step failed (original exception unavailable)")
