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
