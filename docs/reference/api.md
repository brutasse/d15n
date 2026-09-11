# API reference

The public API is small on purpose. Everything you need to write workflows
is four callables and one exception; the rest is what you use to observe
runs and steps. Rendered from the source.

## Core

The four core callables live in `d15n.api` and are re-exported from the
`d15n` package (lazily, so importing `d15n` does not import Django). The
blocks below document them at their definition site.

::: d15n.api.workflow

::: d15n.api.step

::: d15n.api.parallel

::: d15n.api.schedule

::: d15n.Terminal

## Execution context

::: d15n.context.current

::: d15n.context.Context

## Exceptions

::: d15n.errors.D15nError

::: d15n.errors.WorkflowCodeError

::: d15n.errors.StepFailure

::: d15n.errors.SimulatedCrash

::: d15n.errors.DrainOrphan

## Stored exceptions

::: d15n.serde.encode_exception

::: d15n.serde.decode_exception

## Worker

::: d15n.worker.Worker
