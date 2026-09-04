# D15n - Durable workflow execution for Django

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

- Results from previous steps available for consumption in the next steps,
  both in the workflow body and from within a step (see Reading previous
  step results).

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

### Naming steps

By default a step is identified by its position in the body (a dotpath like
`3`, or `3.1.2` within a `parallel`). Positions shift when steps are added or
removed, which breaks replay of in-flight workflows on changed code. To keep
a step's identity stable, pass `d15n_id="..."` at the call site:

```python
@workflow
def provision_vm(args):
    vm_id = create_vm(args["name"], args["size"], d15n_id="create-vm")
    ip, _sg = parallel(
        lambda: attach_ip(vm_id, d15n_id="attach-ip"),
        lambda: setup_security_group(vm_id, args["name"], d15n_id="setup-sg"),
        d15n_id="fanout",
    )
    return ip
```

- The name becomes the step's dotpath segment (`create-vm`,
  `fanout.0.attach-ip`). Unnamed steps still take the next position, so
  naming existing steps shifts no other ids and a named step's id never
  depends on position.
- `d15n_id` must be unique per scope — one body, or one parallel branch —
  and a step's name and a `parallel` fork's name share the scope. Reusing a
  name raises `D15nError` before the step runs. The same name is fine in
  different branches.
- Names may be dynamic (`d15n_id=f"iter-{i}"` inside a loop).
- Renaming a step gives it a new identity: the old record is orphaned and
  the step re-executes on the next claim.
- A name must be a non-empty string without dots or whitespace, not purely
  numeric, and the full id must fit in 300 characters.

### Reading previous step results

A step can read the result of any step that has already completed in the
current run, on both a fresh pass and a replay. The run keeps the shared
outcome store current — each step's outcome is available as soon as it is
recorded — so a step only ever needs to look at the current context:

```python
from d15n import context, step


@step
def create_vm(name, size):
    return cloud_api.create_vm(name=name, size=size)


@step
def tag_vm():
    vm_id = context.current().outcomes["create-vm"]["result"]
    cloud_api.tag(vm_id, "provisioned")
    return None


@workflow
def provision_vm(args):
    create_vm(args["name"], args["size"], d15n_id="create-vm")
    tag_vm()
    return None
```

- `context.current().outcomes` maps each step id to a dict of `name`,
  `status` (`"done"` or `"failed"`), `result` and `error`. A completed
  step's value is `.outcomes[step_id]["result"]`; a failed step has
  `result=None` and its encoded `error` set.
- Steps are keyed by their step id: a top-level step is keyed by its
  `d15n_id` (e.g. `"create-vm"`), an unnamed step by its positional dotpath
  (`"1"`, `"2"`, ...). Name the steps you read, so the lookup is stable
  across code edits.
- A step sees every step that completed before it starts, including steps
  recorded earlier in the same pass. It never sees its own result — that is
  only recorded after the step returns.
- Within a `parallel`, sibling branches run concurrently, so a branch must
  not read a sibling's outcome: completion order is not guaranteed, and a
  branch's step is keyed by its full dotpath (e.g. `"1.0.attach-ip"`). Read
  sibling values from `parallel`'s return tuple instead; steps from an
  earlier `parallel` or earlier sequential steps are safe to read.
- Treat `.outcomes` as read-only; the engine maintains it.

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
python manage.py d15n_worker --pool 8 --poll 0.2 --name d15n-runner-0
```

Workers require PostgreSQL. They poll for due workflows
(`SELECT ... FOR UPDATE SKIP LOCKED`) and run them on a thread pool.

Each worker runs under a stable **name** (default: the hostname). The name
must be identical across restarts and unique among concurrently running
workers — a k8s StatefulSet gives you both for free, since each pod has a
fixed name. On startup a worker re-claims the workflows it was running when
it last went away (matched by name) and resumes them by replay; in steady
state it only claims new scheduled workflows.

**Drawback:** recovery is by identity, not by time. A workflow is only ever
re-claimed by a worker with the same name. If that name never comes back
(the StatefulSet is scaled down or deleted), its in-flight workflows are
orphaned — no other worker will steal them. Re-run them manually or point a
worker at the orphaned name.

### Semantics

- Recovery is replay-based: on any claim the workflow body is re-run from the
  top, recorded step outcomes are served from the database, and execution
  resumes at the first unrecorded step. Code between step calls therefore
  re-runs on every resume and must be deterministic; the engine records each
  step's qualified name and fails the workflow loudly if the body diverges.
  A step's identity is its dotpath: its position for unnamed calls, or its
  `d15n_id` name (see Naming steps).
- Steps are at-least-once: if a worker dies after a step's side effect but
  before its record is written, the step re-runs on recovery. Make side
  effects idempotent or keyed. Retries and backoff are your responsibility.

### Idempotent or keyed side effects

A step that re-runs after a crash performs its side effect twice. There are
two ways to make that harmless:

- Idempotent: the effect is safe to repeat, so the second execution leaves
  the system in the same state as the first.

  ```python
  @step
  def tag_vm(vm_id):
      cloud_api.set_tags(vm_id, {"team": "data"})  # re-run writes the same tags
  ```

  Setting a value, deleting something (the `delete_vm` cleanup above), or
  converging SQL (`INSERT ... ON CONFLICT DO UPDATE`,
  `CREATE INDEX IF NOT EXISTS`) are idempotent: run the line once or twice,
  the end state is the same.

- Keyed: the effect is not safe to repeat, so it is sent with a stable key
  that the receiving system uses to recognize and suppress the duplicate.

  ```python
  @step
  def charge_order(order_id, amount):
      return billing_api.charge(
          order_id, amount, idempotency_key=f"charge:{order_id}"
      )
  ```

  If the worker dies after the charge is settled but before the step is
  recorded, recovery re-runs the step with the same key and the billing API
  returns the original charge instead of charging again. The same pattern
  covers HTTP `Idempotency-Key` headers, uniquely named resources, or
  `INSERT ... ON CONFLICT DO NOTHING` on a unique column.

The key must be identical on every replay of the same step. Derive it from
the workflow's arguments when the key names a logical operation (one charge
per order), or from the run id when it names a single attempt
(`d15n.context.current().workflow_id`). Timestamps and `uuid4()` do not
work: a re-run computes a new key, and the duplicate passes.

A plain `cloud_api.create_vm(name, size)`, an unkeyed charge, or an unkeyed
e-mail are neither idempotent nor keyed: each re-run provisions another VM,
charges the order again, or sends the e-mail again.

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

- CI/CD on gha
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
- visualization / graph via AST parsing
- workflow metrics: pool utilization, workflow processing health
