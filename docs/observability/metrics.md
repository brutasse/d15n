# Metrics

d15n exposes Prometheus metrics for workflow processing health and runner
health. Install the extra to enable them; without it, all metric recording
is a no-op.

```
pip install "d15n[metrics]"
```

All d15n collectors register in the **default** `prometheus_client`
registry, so a metrics endpoint in the host application already serves
in-process d15n metrics (for example from an embedded worker) with no
configuration.

## Serving the metrics

**Runner endpoint.** A worker can serve the metrics of its own process —
runner health, pool utilization, and per-workflow execution — on an HTTP
endpoint:

```
python manage.py d15n_worker --metrics-port 9117
```

Prometheus then scrapes `http://<runner>:9117/`. The endpoint is
unauthenticated; keep it behind network segmentation. `--metrics-bind`
controls the interface (default `0.0.0.0`, for in-cluster scraping).

**Host application.** To also expose the queue state computed from the
database, mount the provided view:

```python
# urls.py
from d15n import views

urlpatterns = [
    path("d15n/metrics", views.metrics_view),
]
```

It refreshes the queue gauges on every scrape. For a custom scrape handler,
call `d15n.metrics.update_queue_gauges()` before rendering.

## The metrics

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `d15n_workflow_runs_total` | counter | `workflow`, `status` | Runs ended, by final status (`completed`, `failed`, `stopped`). |
| `d15n_workflow_duration_seconds` | histogram | `workflow`, `status` | Wall time from claim to terminal state. |
| `d15n_step_runs_total` | counter | `workflow`, `step`, `status` | Step executions, by outcome (`done`, `failed`). |
| `d15n_step_duration_seconds` | histogram | `workflow`, `step` | Step execution time. |
| `d15n_worker_pool_size` | gauge | `runner` | The worker's thread pool size. |
| `d15n_worker_inflight` | gauge | `runner` | Workflows currently in flight in this worker. |
| `d15n_worker_claims_total` | counter | `runner` | Workflows claimed (including startup catchup). |
| `d15n_worker_orphans_total` | counter | `runner` | Workflows orphaned when the drain deadline expired. |
| `d15n_worker_started_at_seconds` | gauge | `runner` | Unix time the worker started (uptime). |
| `d15n_workflows_pending` | gauge | — | Workflows scheduled and waiting for a claim. |
| `d15n_workflows_running` | gauge | — | Workflows currently running. |
| `d15n_workflows_oldest_pending_age_seconds` | gauge | — | Age of the oldest scheduled workflow. |

The queue gauges (`d15n_workflows_*`) are computed from the database at
scrape time, not by any single process.

## Label cardinality

Labels are bounded to code-defined names — workflow and step **function
names**, and the runner name — never to per-run identifiers. The metric
series count grows with the number of workflows and steps you define, not
with the number of runs.
