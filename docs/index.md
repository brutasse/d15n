# d15n

Durable workflow execution for Django.

d15n provides a syntax and an execution environment for durable workflows:
multi-step processes that effect actions on external systems while keeping
your database consistent. A workflow is plain Python — a function that calls
`@step` functions, sequentially or through `parallel()` forks. Runs are
stored in SQL through Django models, executed by workers that claim work from
the database, and resume after any interruption — worker restart, crash,
rolling deploy — by replaying the body and serving recorded step outcomes
from the database.

**Durable is a guarantee of completion, not a guarantee of success.** If a
step fails, the workflow fails; retrying is up to you. Robustness aspects
such as retries, backoff and compensation are your application's
responsibility, and the [side effects guide](guides/side-effects.md) explains
what to do about at-least-once execution.

## Properties

- **Durable storage in SQL**, with Django as the integration target. A run is
  a row; each step's outcome is a row.
- **Sequential or parallel structure.** Steps run in order; `parallel()` runs
  branches concurrently, and branches may themselves be sequences of steps or
  nested forks.
- **Immediate start, no queue.** A pool of workers claims due workflows as
  early as scheduled. Queue semantics — buffering, prioritization — are not a
  goal.
- **Transactional scheduling.** A workflow is scheduled with the other
  database changes that led to it being scheduled: one transaction, commit or
  rollback together.
- **Results flow forward.** A step can read the result of any step that has
  already completed in the run, both in the workflow body and from within a
  step.

## What d15n is not

- Not a task queue: there is no backlog to drain, no priority, no rate
  limiting. Work is picked up as early as it is due.
- Not a scheduler in the cron sense: you call `schedule()` when the event
  that drives the workflow happens.
- No built-in retry: a failed run stays failed. Decide what to do with it —
  reschedule with different arguments, drop it, page someone.

## How it works, in a paragraph

You define `@step` functions (units of work whose outcomes are persisted) and
combine them in an `@workflow` function. `schedule(workflow, args)` inserts a
row into your current transaction. A worker claims the row with
`SELECT ... FOR UPDATE SKIP LOCKED`, re-runs the workflow body from the top,
serves every already-recorded step outcome from the `Step` table, and executes
only the first unrecorded step and on. If the worker dies at any point —
mid-step, between steps, mid-replay — the next claim of the same workflow
replays and picks up exactly where the previous one left off.

## Where to go next

- [Quickstart](getting-started.md) — install, first workflow, first worker,
  in ten minutes.
- [How it works](concepts/index.md) — the execution model, replay, and what
  at-least-once means for your code.
- [Running](running/workers.md) — workers, deployment, rollouts, operations.
- [Reference](reference/api.md) — the public API, the data model, thread
  safety guarantees.
