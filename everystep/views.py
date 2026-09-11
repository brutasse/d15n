"""Django views for host applications."""

import datetime
import decimal
import json
import time
import uuid as uuid_lib

from django.db.models import Count
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse

from everystep import graph, metrics, serde, ui
from everystep.models import Step, Workflow

_RUNS_LIMIT = 200
_SSE_POLL_INTERVAL = 1.0
_SSE_PING_EVERY = 15


def metrics_view(request):
    """Serve everystep's Prometheus metrics, refreshing the queue gauges from the
    database on each scrape."""
    return HttpResponse(metrics.render_latest(), content_type=metrics.content_type)


def ui_view(request):
    """Serve the self-contained everystep UI page."""
    base = request.path if request.path.endswith("/") else request.path + "/"
    return HttpResponse(ui.PAGE.replace("__EVERYSTEP_BASE__", base), content_type="text/html")


class _PayloadEncoder(json.JSONEncoder):
    def default(self, o):
        return _json_default(o)


def _json_default(obj):
    """Serialize everystep's extended types the way they are stored, then plain
    types by value."""
    try:
        return serde.json_default(obj)
    except TypeError:
        if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
            return obj.isoformat()
        if isinstance(obj, (uuid_lib.UUID, decimal.Decimal)):
            return str(obj)
        raise


def _runs_payload(status=None):
    """The runs snapshot: recent runs with step counts, plus the runners
    with in-flight work."""
    qs = Workflow.objects.all()
    if status in dict(Workflow.Status.choices):
        qs = qs.filter(status=status)
    counts = {}
    rows = Step.objects.values("workflow_id", "status").annotate(n=Count("id"))
    for row in rows:
        counts.setdefault(row["workflow_id"], {"done": 0, "failed": 0})[row["status"]] = row["n"]
    runs = []
    for wf in qs.order_by("-created_at")[:_RUNS_LIMIT]:
        c = counts.get(wf.id, {"done": 0, "failed": 0})
        static, _reason = graph.build_graph(wf.name)
        total = graph.step_count(static) if static is not None else c["done"] + c["failed"]
        runs.append(
            {
                "id": str(wf.id),
                "name": wf.name,
                "status": wf.status,
                "claimed_by": wf.claimed_by,
                "created_at": wf.created_at.isoformat(),
                "completed_at": wf.completed_at.isoformat() if wf.completed_at else None,
                "steps": {"total": total, "done": c["done"], "failed": c["failed"]},
            }
        )
    runners = list(
        Workflow.objects.filter(status=Workflow.Status.RUNNING, claimed_by__isnull=False)
        .values("claimed_by")
        .annotate(inflight=Count("id"))
        .order_by("-inflight", "claimed_by")
    )
    return {"runs": runs, "runners": runners}


def api_runs_view(request):
    """Recent workflow runs as JSON. `?status=` filters by run status."""
    return JsonResponse(_runs_payload(request.GET.get("status")), encoder=_PayloadEncoder)


def api_run_view(request, run_id):
    """One run: the annotated step graph and its recorded steps."""
    try:
        run = Workflow.objects.get(id=run_id)
    except Workflow.DoesNotExist:
        return JsonResponse({"error": "run not found"}, status=404)
    steps = list(Step.objects.filter(workflow_id=run.id).order_by("pk"))
    static, reason = graph.build_graph(run.name)
    if static is not None:
        annotated, summary = graph.annotate(
            static,
            {s.step_id: s.status for s in steps},
            run.status == Workflow.Status.RUNNING,
        )
        detail = {"supported": True, "nodes": annotated, **summary}
    else:
        detail = {"supported": False, "reason": reason}
    return JsonResponse(
        {
            "run": {
                "id": str(run.id),
                "name": run.name,
                "status": run.status,
                "claimed_by": run.claimed_by,
                "args": run.args,
                "result": run.result,
                "error": run.error,
                "created_at": run.created_at.isoformat(),
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            },
            "graph": detail,
            "steps": [
                {
                    "step_id": s.step_id,
                    "name": s.name,
                    "status": s.status,
                    "args": s.args,
                    "kwargs": s.kwargs,
                    "result": s.result,
                    "error": s.error,
                }
                for s in steps
            ],
        },
        encoder=_PayloadEncoder,
    )


def api_stream_view(request):
    """Server-Sent Events: the runs snapshot, re-sent on every change."""

    def stream():
        last = None
        idle = 0
        while True:
            data = json.dumps(_runs_payload(), default=_json_default, sort_keys=True)
            if data != last:
                yield f"data: {data}\n\n".encode()
                last = data
                idle = 0
            else:
                idle += 1
                if idle >= _SSE_PING_EVERY:
                    yield b": ping\n\n"
                    idle = 0
            time.sleep(_SSE_POLL_INTERVAL)

    response = StreamingHttpResponse(stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
