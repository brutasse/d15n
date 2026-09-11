# Workers

A worker is a long-lived process that claims due workflows and executes them
on a thread pool:

```
python manage.py everystep_worker --pool 8 --poll 0.2 --name everystep-runner-0
```

## The loop

Every `--poll` seconds the worker:

1. reaps finished runs (and logs any run that crashed outside the runner);
2. computes free capacity: `--pool` minus the runs currently in flight;
3. claims up to that many scheduled workflows — `SELECT ... FOR UPDATE SKIP
   LOCKED` in a single transaction, so concurrent workers never claim the
   same row;
4. submits each claimed workflow to its thread pool.

A worker never holds more than `--pool` workflows at once, and claims nothing
new while the pool is full. There is no queue in front of it: a scheduled
workflow is picked up as early as `--poll` allows, by whatever worker has
capacity.

Workers run on **PostgreSQL and MariaDB**. On PostgreSQL the claim uses
`FOR UPDATE SKIP LOCKED`, so concurrent workers never block each other; on
MariaDB it uses a plain `FOR UPDATE`, so claims stay exclusive but serialize
while a batch is being locked.

## The name

Each worker runs under a stable **name** (`--name`, default: the hostname).
The name is the unit of recovery, and the contract is:

- **identical across restarts** — on startup the worker re-claims the
  workflows it was running when it last went away, matched by name, and
  resumes them by replay;
- **unique among concurrently running workers** — two live workers with the
  same name will both try to resume the same runs.

A k8s StatefulSet gives you both properties for free: each pod has a fixed,
unique name.

## Startup catchup

Before entering the poll loop, the worker runs one **catchup**: it claims
back its own in-flight workflows — `status = running AND claimed_by = <name>`,
up to pool size — and submits them for replay. In steady state the loop only
claims newly scheduled workflows; catchup is how a restart picks up exactly
what the previous process with that name left behind.

## What happens to a run

A run executes on a pool thread. The engine's behaviour — replay, recording,
terminal transitions — is described in
[how it works](../concepts/index.md). Two worker-level behaviours to know:

- If a run dies from something the runner does not handle (a database error
  mid-record, a bug in the engine rather than in your steps), the worker logs
  it, marks the run `failed` with the encoded exception, and reports it to
  Sentry if configured. The worker itself keeps running.
- If the run's row no longer exists (deleted out from under it), the worker
  logs a warning and moves on.

## Embedded workers

`everystep.worker.Worker` is a plain class; you can run a worker inside a host
process instead of a standalone management command:

```python
from everystep.worker import Worker

worker = Worker(pool_size=4, poll=0.2, name="embedded")
thread = threading.Thread(target=worker.run, daemon=True)
thread.start()
# ... later:
worker.stop()
```

Two differences from standalone mode:

- signal handlers are installed only when `run()` executes on the main
  thread; an embedded worker must be stopped with `worker.stop()`;
- when the drain deadline expires with work still in flight, a standalone
  worker exits the process; an embedded one simply returns and leaves the
  process lifecycle to its host.
