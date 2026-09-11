# Thread safety and guarantees

d15n executes workflows on a thread pool and `parallel` branches on further
threads. This page states what is shared, what is isolated, and what the
guarantees rest on.

## Isolated per workflow

- Each claimed workflow executes on **its own pool thread** of the worker.
- The execution context (`d15n.context.Context`) lives in **thread-local
  storage**: the engine sets it on the thread that runs the body and clears
  it on exit. A step sees the context of the thread it is running on, via
  `d15n.context.current()`.
- Each in-flight workflow has **its own `Context` instance** — its own
  step-id counter and prefix state. Two workflows never share context state,
  no matter how many threads the worker runs.
- The **workflow body runs single-threaded** for that workflow, except
  inside `parallel()` forks.

## Inside `parallel()`

- One pool thread per branch; the fork waits for all of them.
- Each branch thread is given a **child `Context`**: a new scope (its own
  counter and id prefix `<fork>.<index>.`), but **sharing** the outcome
  store, the draining flag, and the workflow identity with the parent and
  with its sibling branches.
- The parent thread's context is **not** visible on branch threads —
  thread-locals do not cross threads. A branch thread that calls
  `context.current()` sees its own branch context.

## The shared outcome store

The `outcomes` dict is shared by all branches of a run and is read and
written concurrently:

- each step **writes its own key once**, as a fresh dict — no in-place
  mutation of stored outcomes;
- reads are plain dict lookups.

This is safe under CPython because dict operations are atomic under the GIL.
d15n holds **no locks of its own**; that is the whole synchronization story
for in-process state. If you rely on it, keep running CPython.

## Database connections

Django connections are thread-local. Worker pool threads and `parallel`
branch threads therefore open their own connections, and the worker closes
them:

- after **each workflow** a pool thread executes, so long-lived pool threads
  do not accumulate one connection per run;
- after **each `parallel` branch**, for the same reason on branch threads.

You do not need to manage connections from step code; the worker handles the
lifecycle.

## What d15n does not synchronize

- **Step functions.** d15n does not serialize step calls. Steps from
  different workflows (within one worker's pool) and steps from different
  `parallel` branches execute **concurrently**. A step that touches shared
  resources — a global cache, an HTTP session, a file — must be safe for
  concurrent invocation with its own synchronization, exactly as any
  concurrent Python code. d15n gives you no help and no protection here.
- **Code you write in workflow bodies** runs on the workflow's thread and
  does not race with anything inside d15n; it may only observe other
  workflows' side effects through the external systems they touch.

## Across processes

Correctness between workers comes from the **database**, not from in-process
state:

- **Claims** use `SELECT ... FOR UPDATE SKIP LOCKED`, so concurrent workers
  take disjoint sets of rows;
- **Recovery** matches on the worker name — a run is re-claimed only by a
  worker with the same `claimed_by`;
- **Terminal transitions** are conditional updates from `running` only, so a
  stale write cannot clobber a run that has moved on;
- **Idempotency keys** and **step identities** are enforced by unique
  constraints.

## Calling d15n from your own threads

- A `@step` called on a thread **without** a context (outside a running
  workflow) executes as a plain function with no recording.
- Do not call `@step`/`@workflow` from raw threads you spawn inside a
  workflow and expect them to be recorded: the context is per-thread, and a
  thread you created has none. Use `parallel()` for concurrency inside a
  run — it is the only supported way to fan work out across threads.

## Test hook

`d15n.runner.fault` is a process-wide global, consulted between a step's
side effect and its record. It exists for the test suite (see
[testing](../development/testing.md)); leave it `None` in production.
