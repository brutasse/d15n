# Reading previous step results

A step can read the result of any step that has already completed in the
current run — on a fresh pass and on a replay — through the current
execution context:

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

## The outcome store

`context.current().outcomes` maps each step id to a dict:

```python
{
    "name": "provision.create_vm",   # qualified function name
    "status": "done",                # "done" or "failed"
    "result": {...},                 # None when the step failed
    "error": None,                   # encoded exception when it failed
}
```

The store is kept **current** by the engine: it is seeded with everything
recorded before the current claim, and each step's outcome is added as soon
as it is recorded. So a step sees every step that completed before it starts,
including steps recorded earlier in the same pass, and it sees the same thing
on a replay.

Rules:

- A step **never sees its own result** — that is only recorded after the
  step returns.
- Steps are keyed by **step id**: a named step by its `d15n_id` (e.g.
  `"create-vm"`), an unnamed step by its positional dotpath (`"1"`, `"2"`,
  ...). Name the steps you read, so the lookup is stable across code edits.
- Within a `parallel`, sibling branches run concurrently: **do not read a
  sibling's outcome**. Completion order is not guaranteed, and a branch's
  step is keyed by its full dotpath (e.g. `"fanout.0.attach-ip"`). Read
  sibling values from `parallel`'s return tuple. Steps from an earlier
  `parallel` or from earlier sequential steps are safe to read.
- Within a sequence branch, steps run in order, so a later step can read an
  earlier step's outcome from the same branch.
- Treat `.outcomes` as **read-only**; the engine maintains it.

## Where it helps

- A step that needs another step's output without it being threaded through
  the workflow body's local variables.
- A step that wants to branch on how a previous step ended: check
  `.outcomes[step_id]["status"]` and read the encoded `error` if it failed.
- Building up state across a long pipeline without carrying it as arguments.
