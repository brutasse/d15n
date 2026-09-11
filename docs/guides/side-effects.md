# Idempotent or keyed side effects

A step that re-runs after a crash performs its side effect **twice** — the
at-least-once window is described in
[how it works](../concepts/index.md#durability-at-least-once). There are two
ways to make that harmless.

## Idempotent

The effect is safe to repeat: the second execution leaves the system in the
same state as the first.

```python
@step
def tag_vm(vm_id):
    cloud_api.set_tags(vm_id, {"team": "data"})   # re-run writes the same tags
```

Setting a value, deleting something (the `delete_vm` cleanup from the
[errors page](../concepts/errors.md#durable-cleanup)), or converging SQL
(`INSERT ... ON CONFLICT DO UPDATE`, `CREATE INDEX IF NOT EXISTS`) are
idempotent: run the line once or twice, the end state is the same.

## Keyed

The effect is not safe to repeat, so it is sent with a **stable key** that
the receiving system uses to recognize and suppress the duplicate.

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

## Choosing the key

The key must be **identical on every replay of the same step**:

- **From the workflow's arguments** when the key names a logical operation —
  one charge per order, one shipment per order line. Survives re-runs of the
  whole workflow, which is what you want.
- **From the run id** when the key names a single attempt of this run:
  `context.current().workflow_id`. The id is a UUIDv7 fixed when the run was
  scheduled.

These do **not** work:

- timestamps — a re-run computes a new key, and the duplicate passes;
- `uuid4()` — same problem, for the same reason.

## What is neither

A plain `cloud_api.create_vm(name, size)`, an unkeyed charge, or an unkeyed
e-mail is neither idempotent nor keyed: each re-run provisions another VM,
charges the order again, or sends the e-mail again. If the API cannot take a
key and the operation cannot be made convergent, that is a property of the
integration to solve — wrap it in a keyed operation at the boundary (a
"reservation" row, a request id you generate once and persist), not inside
the step.
