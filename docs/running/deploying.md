# Deploying and operating

## Deployment

A deployment is a set of worker processes, each with a **stable, unique
name**, pointed at the same database.

### Kubernetes: StatefulSet

A StatefulSet is the natural fit, because each pod has a fixed, unique name
that survives restarts:

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: d15n-runner
spec:
  replicas: 3
  serviceName: d15n-runner
  template:
    spec:
      terminationGracePeriodSeconds: 90   # > --drain
      containers:
        - name: runner
          image: your-image
          args: ["d15n_worker", "--pool", "8", "--name", "$(POD_NAME)",
                 "--drain", "30"]
          env:
            - name: POD_NAME
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
          ports:
            - containerPort: 9117        # if --metrics-port 9117
```

- Scale by changing `replicas`; each replica is an independent claimer.
- `terminationGracePeriodSeconds` must be **greater** than `--drain` so the
  worker can exit cleanly before the pod is killed.

### systemd

A plain service unit per worker, with distinct names:

```ini
[Service]
ExecStart=/usr/bin/python /app/manage.py d15n_worker --pool 8 --name d15n-runner-0 --drain 30
TimeoutStopSec=90
```

`TimeoutStopSec` plays the same role as the k8s grace period: it must exceed
`--drain`.

## Rollouts (SIGTERM drain)

On `SIGTERM` (or `SIGINT`) a worker does not abort its work. It:

1. **stops claiming** — no new workflow is taken in;
2. **drains** — the step in flight in each of its workflows runs to the end
   and is recorded, but **no new step starts**: at the next step boundary the
   engine raises an internal control-flow exception and leaves the workflow
   `running`, parked at a step boundary with everything so far recorded;
3. **waits** for the in-flight steps up to `--drain` seconds, then exits.
   Anything still running is **orphaned** (see below).

`--drain 0` waits for in-flight work indefinitely.

A worker coming up under the same name re-claims those parked workflows at
startup and resumes them by replay, picking up exactly where the old one left
off. A rolling deploy with a StatefulSet is therefore lossless: pods are
restarted one at a time, each under its own name.

**Tuning `--drain`:**

- **below** the supervisor's grace period (k8s
  `terminationGracePeriodSeconds`, systemd `TimeoutStopSec`) — or the worker
  gets killed mid-step, which is fine for correctness (the step re-runs) but
  defeats the point of draining;
- **at or above** your longest-running step — so every in-flight step gets to
  finish and be recorded, and no run is needlessly orphaned.

## Orphans and recovery

!!! warning
    Recovery is **by identity, not by time**. A workflow is only ever re-claimed
    by a worker with the **same name**.

If that name never comes back — the StatefulSet is scaled down or deleted, a
unit is removed — its in-flight workflows are **orphaned**: no other worker
will steal them. Signals that this happened:

- the worker's shutdown log: `drain deadline of Ns expired with M workflow(s)
  still in flight; they are orphaned and will be picked up by the next worker
  named ...`
- the `d15n_worker_orphans_total` metric for that runner.

Two remedies:

- **point a worker at the orphaned name** (`--name <that-name>`): its startup
  catchup claims the parked workflows and resumes them by replay;
- **re-schedule** the work with fresh arguments and, if you use them, fresh
  idempotency keys — the old run keeps its row.

Both are manual; d15n deliberately does not guess which orphaned work is
still worth doing.

## Data retention

d15n never deletes its own rows. Each run keeps its `Workflow` row plus one
`Step` row per executed step, forever. If that growth matters, prune or
archive from the application side — for example a periodic job that drops
terminal runs older than N days. The UI shows the 200 most recent runs
regardless.
