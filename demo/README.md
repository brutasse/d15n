# d15n demo

A throwaway Django project that shows the d15n UI with a live stream of
sample workflows.

## Run it

```
docker-compose up --build
```

Then open <http://localhost:8000/> (it redirects to the UI at `/d15n/`).

Within seconds the board is populated with a mix of completed, running,
failed and scheduled runs, and new runs keep arriving: the `seeder`
service feeds a small random batch every few seconds (the list shows the
latest 200 runs). Click a run to see its step DAG — named steps,
`parallel` forks fanning out and back in (including a nested one),
in-flight steps pulsing, failed steps in red; click a node for the step's
args, result, or error.

## What runs

| workflow | what it shows |
| --- | --- |
| `jobs.workflows.order_processing` | a named sequential pipeline |
| `jobs.workflows.fan_out` | a fork with single-step and two-step sequence branches |
| `jobs.workflows.nested_fan_out` | a fork inside a fork |
| `jobs.workflows.release_pipeline` | two parallel stages in sequence: checks → builds → publish |
| `jobs.workflows.slow_pipeline` | three ~2s steps: in-flight status and progress |
| `jobs.workflows.slow_fan` | three ~3s parallel branches: several in-flight nodes at once |
| `jobs.workflows.big_report` | ten sequential steps |
| `jobs.workflows.risky_transfer` | fails about half the time |
| `jobs.workflows.flaky_parallel` | one of three parallel branches fails about half the time |

## Services

- `db` — Postgres 16.
- `web` — Django dev server on :8000, serving the UI.
- `worker` — a d15n runner (`d15n_worker`, pool of 4) that claims and
  executes runs.
- `seeder` — schedules an initial burst, then keeps feeding new runs.

Each app container runs `migrate` (with retries) before starting, so the
startup order does not matter.

## Tuning

Re-seed or tune the feed on a running setup:

```
docker compose exec seeder python manage.py feed_demo --burst 10 --interval 2 --drip 4
```

`feed_demo --help` lists the options (`--once` schedules the burst and
exits).

## Add a workflow

Define `@workflow` / `@step` functions in `jobs/workflows.py` — straight-line
bodies (step calls and `parallel` forks) get a rendered DAG, anything
else falls back to the recorded steps. Then add the workflow to the seeder's
`POOL` in `jobs/management/commands/feed_demo.py` and an `args` builder in
`_args_for`.

Not for production: `DEBUG=True`, an insecure secret key, and no
authentication on the UI or the database.
