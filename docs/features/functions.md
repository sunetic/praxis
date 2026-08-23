# Function

A Function packages a well-defined database task as a reusable execution unit. Compared with an Agent, it emphasizes explicit inputs, testable outputs, and published versions, making it suitable for more deterministic queries, calculations, and controlled operations.

## Build workflow

1. Create a Function and describe its goal, inputs, and outputs.
2. Use Build Chat to have the builder Agent generate or revise a draft.
3. Select representative datasources and parameters, then inspect the real business result in the test panel.
4. Correct problems until the result meets its acceptance criteria.
5. Publish a runnable version.

Draft and published versions are separate states. Build-time tests use a constrained path, while production calls and Schedulers use the published version. Editing a draft does not alter the version currently in use.

## Ways to use a Function

- Enter parameters and call it directly from the Function list.
- Inspect run history, status, duration, and output.
- Trigger it periodically with Scheduler.

## Acceptance checks

Do not stop at “the run succeeded.” Check input boundaries, returned fields, empty data, error paths, query scope, execution time, and whether write behavior matches expectations. For real changes, validate first with non-production data and retain an explicit confirmation and rollback plan.
