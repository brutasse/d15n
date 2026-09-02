# D15n - Durable execution

This library provides a syntax and execution environment for durable workflows
and effecting actions to external systems while maintaining database
consistency. Durable is meant as a guarantee of completion, not a guarantee of
success. Robustness aspect like retries and backoff are user responsibilities.

High-level workflow properties:

- Durable storage in SQL, with Django as initial integration target.

- Ability to organize workflows along combinations of sequential or parallel
  steps.

- Immediate start for scheduled workflows: a pool of workers ought to be ready
  to pick up work as early as scheduled. Queue semantics are not a goal.

- Transactional scheduling: workflows are meant to be scheduled along with the
  other database changes that led to it being scheduled.

- Results from previous steps available for consumption in the next steps.

- Try/rescue semantics to allow cleanup of database before marking a workflow
  as completed.

## Usage

### Defining workflows and steps

```python
from d15n import parallel, schedule, step, workflow


@step
def create_vm(name, size):
    return cloud_api.create_vm(name=name, size=size)


@step
def attach_ip(vm_id):
    return cloud_api.attach_ip(vm_id)


@step
def setup_security_group(vm_id, group):
    return cloud_api.create_security_group(vm_id, group)


@workflow
def provision_vm(args):
    vm_id = create_vm(args["name"], args["size"])
    ip, _sg = parallel(
        lambda: attach_ip(vm_id),
        lambda: setup_security_group(vm_id, args["name"]),
    )
    return ip
```

- `@workflow` functions are scheduled by name; their return value is the
  persisted workflow result.
- `@step` functions are units of work whose outcomes (result or exception)
  are persisted in SQL.
- Calling steps or workflows outside a running workflow runs them as plain
  functions with no recording, so both are trivially unit-testable.
- Step and workflow arguments and results must be JSON-serializable
  (`datetime`, `date`, `timedelta`, `UUID`, `bytes` and `enum` are
  supported). Store IDs, not model instances.
- Step exceptions are persisted and re-raised on replay, so durable cleanup
  is plain Python:

```python
@workflow
def provision_vm(args):
    vm_id = create_vm(args["name"], args["size"])
    try:
        ip = attach_ip(vm_id)
    except Exception:
        delete_vm(vm_id)  # a regular step; replay-safe
        raise
    return ip
```

`parallel(*branches)` takes zero-arg callables (usually lambdas calling one
`@step`), runs them concurrently and returns their results in order. If any
branch raises, the single error is re-raised (one failing branch) or an
`ExceptionGroup` is raised (several).

### Scheduling

```python
from django.db import transaction
from d15n import schedule

with transaction.atomic():
    order.save()
    schedule(
        provision_vm,
        {"name": order.vm_name, "size": order.vm_size},
        idempotency_key=f"order:{order.pk}",  # optional
    )
```

Scheduling is a plain INSERT into the caller's transaction: on commit the
workflow becomes claimable by workers, on rollback it is gone. With an
`idempotency_key`, a repeated schedule with the same key returns the existing
workflow instead of creating a new one.

### Running workers

```
python manage.py d15n_worker --pool 8 --poll 0.2 --lease 300
```

Workers require PostgreSQL. They poll for due workflows
(`SELECT ... FOR UPDATE SKIP LOCKED`) and run them on a thread pool. The
lease is refreshed on every recorded step; a workflow whose lease expired is
re-claimable by any worker, which resumes it by replay. The lease must be
longer than the longest single step.

### Semantics

- Recovery is replay-based: on any claim the workflow body is re-run from the
  top, recorded step outcomes are served from the database, and execution
  resumes at the first unrecorded step. Code between step calls therefore
  re-runs on every resume and must be deterministic; the engine records each
  step's qualified name and fails the workflow loudly if the body diverges.
- Steps are at-least-once: if a worker dies after a step's side effect but
  before its record is written, the step re-runs on recovery. Make side
  effects idempotent or keyed. Retries and backoff are your responsibility.

## Development

- `uv sync` — create the venv and install dependencies.
- `uv run pytest` — run the test suite. It starts a throwaway Postgres
  container on a free port and removes it afterwards. Point it at your own
  server with the `D15N_TEST_PG_PORT` env var (override the image with
  `D15N_TEST_PG_IMAGE`, default `postgres:16`).
- Tests can simulate a worker process dying between a step's side effect and
  its record: set `d15n.runner.fault` to a handler `fault(ctx, step_id)`
  that raises `d15n.errors.SimulatedCrash`.

## Roadmap

- document context access (results of previous steps)
- CI/CD on gha
- remove leases in favor of deterministic runner names that get picked up
  again after restart (k8s statefulset pattern)
- document/add sugar for sequential steps in parallel (wrapping step that
  invokes the sequence in order or arrays of steps for each sequence in the
  parallel() call)
- switch workflow UUID to time-based
- document storage limits on result / error size
- document thread safety aspects and guarantees
- battle test runner rollouts: on sigterm, cleanly finish any ongoing step
  until configurable deadline, store results, orphan jobs and shut down. New
  runner coming up picks up where it left
- sentry integration for unhandled excs in workflow runs
