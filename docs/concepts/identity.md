# Step identity

A step's outcome is keyed by a **step id**: a dotpath that pins the call to
a position in the workflow body. The dotpath is what replay matches against,
so what you want is that *the same logical step has the same id across code
versions that must replay in-flight runs together*.

## Positional ids

By default a step is identified by its **position** in the body:

```python
@workflow
def provision_vm(args):
    vm_id = create_vm(args["name"], args["size"])   # step id "1"
    ip, _sg = parallel(
        lambda: attach_ip(vm_id),                   # step id "2.0.1"
        lambda: setup_security_group(vm_id, ...),   # step id "2.1.1"
    )
    return ip
```

Positions shift when steps are added or removed before them: inserting a
step at the top renumbers every step after it, and the next replay of an
in-flight workflow finds a different function at each recorded id and fails
with `WorkflowCodeError`.

## Named ids: `d15n_id`

Pass `d15n_id="..."` at the call site to give a step a stable identity:

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

- The name becomes the step's dotpath segment: `create-vm`, and
  `fanout.0.attach-ip` inside the fork. `parallel()` itself accepts
  `d15n_id`, so the fork's branches get stable ids too.
- A named step **still consumes a position**, so naming (or un-naming) a
  step shifts no other id, and a named step's id never depends on position.
  Inserting unnamed steps around named ones leaves the named ids alone.
- **Renaming** a step gives it a new identity: the old record is orphaned
  (it stays in the table) and the step re-executes on the next claim.

## Scopes

A name must be **unique per scope**. A scope is one workflow body, or one
`parallel` branch — and a step's name and a `parallel` fork's name share the
same scope.

- `create_vm(d15n_id="x")` twice in one body → `D15nError`, raised before
  the second step runs (no side effect).
- A step named `"fanout"` and a `parallel(..., d15n_id="fanout")` in the
  same body → `D15nError`.
- The **same name in different branches is fine**: they produce different
  ids (`fanout.0.x`, `fanout.1.x`).

## Validation

A `d15n_id` must be:

- a non-empty string;
- without dots (dots separate scopes);
- without whitespace;
- not purely numeric (names must not look like positions);
- short enough that the full dotpath fits in 300 characters.

Violations raise `D15nError` before the step runs.

Names may be **dynamic** — `d15n_id=f"iter-{i}"` inside a loop — as long as
the sequence is deterministic across replays, which loops over `args`
derived values are.

## Reading ids

Ids are the keys of `context.current().outcomes` (see
[reading results](results.md)): a top-level named step is keyed by its
`d15n_id`, an unnamed one by its positional dotpath (`"1"`, `"2"`, ...). Name
the steps you read, so the lookup survives edits to the body.
