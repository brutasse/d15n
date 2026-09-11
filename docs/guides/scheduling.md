# Scheduling workflows

```python
from everystep import schedule

run = schedule(provision_vm, {"name": "vm-1", "size": "m1"},
               idempotency_key=None)   # optional
```

`schedule(workflow, *args, idempotency_key=None)` returns the `Workflow`
instance (a UUIDv7 id, time-ordered).

## Transactional

Scheduling is a **plain INSERT into your current transaction**. The workflow
becomes claimable by workers only when the transaction commits; if it rolls
back, the schedule is gone. This is how you keep a workflow consistent with
the data that triggered it:

```python
from django.db import transaction

with transaction.atomic():
    order.save()
    schedule(
        provision_vm,
        {"name": order.vm_name, "size": order.vm_size},
        idempotency_key=f"order:{order.pk}",
    )
```

Nothing is run at schedule time — no thread is started, no code outside the
INSERT executes. The work happens when a worker claims the row.

## Arguments

- `*args` is the positional argument list passed to the workflow function:
  `schedule(wf, a, b)` calls `wf(a, b)`.
- Arguments must be JSON-serializable (the extended types are supported —
  see [steps](../concepts/steps.md#serialization)). They are validated up
  front: `schedule` raises `EverystepError` before inserting anything.
- Arguments are **fixed at schedule time** and replayed verbatim on every
  claim. The workflow name is stored as the function's qualified name
  (`module.qualname`) and resolved by the worker at claim time — from the
  import registry when the worker process imported it, or by importing the
  dotted path.

Passing something that is not an `@workflow` function raises `TypeError`.

## Idempotency keys

With an `idempotency_key`, a repeated — or concurrent — schedule with the
same key **returns the existing workflow** instead of creating a new one
(`get_or_create` under a partial unique constraint on
`(name, idempotency_key)`). Use it to bind a run to the event that caused it:

```python
schedule(ship_order, {"order": order.pk}, idempotency_key=f"ship:{order.pk}")
```

!!! warning
    The key returns the existing run **regardless of its status**. If the run
    for `ship:123` failed, scheduling again with the same key hands you back the
    failed run — it does not restart it. To retry, schedule with a fresh key
    (or delete the old run's row).

## Rescheduling

There is no "retry" verb. To run a workflow again — after a failure, after a
`stopped` hand-off — call `schedule()` again. The previous run keeps its row
and its recorded steps; the new run starts from scratch with its own step
records.
