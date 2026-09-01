# Long-task Reliability

Praxis keeps the long-task path deliberately small. The original user request is the semantic source of truth; the runtime should help the model execute it reliably, not create a second LLM-authored specification that can drift from it.

## Runtime flow

1. The executor receives the original request and available tools.
2. Each tool call produces a paired `tool_start` and `tool_result`. Results are retained as evidence, including failures and timeouts.
3. Recoverable execution failures may use bounded retries. Repeated failure, cancellation, confirmation requirements, and deadlines stop the run with a checkpoint.
4. The executor produces one candidate answer.
5. The executor delivers that answer directly. An optional completion audit can be enabled for diagnostics, but it is disabled by default. When enabled, it cannot call tools, mutate execution or failure state, send repair instructions back into the executor, change an otherwise finished run to partial, or append its feedback to the user answer.
6. The runtime emits an explicit run state and task outcome.

This keeps planning and investigation inside one execution loop. It avoids classifier, verifier-repair, and finalizer loops that add model calls without adding new evidence. Semantic review remains telemetry; external Eval, not the product runtime, measures answer correctness.

## Evidence and tool closure

Material claims in a database task should be traceable to the request or returned tool evidence. The runtime records successful and failed calls so the model can adjust without repeating the same strategy. A recorded failure is execution history, not proof that the final answer is incomplete: later work may supersede it even when the calls do not have matching signatures.

Every emitted `tool_start` must terminate with a `tool_result`. A tool deadline or executor exception is therefore represented as a structured failed result instead of leaving the UI and journal waiting indefinitely. `AGENT_MAX_ELAPSED_SECONDS` sets one total run deadline; `AGENT_TOOL_TIMEOUT_SECONDS` bounds each individual call.

## Completion semantics

Run progress and task success are separate dimensions:

| Field | Values | Meaning |
| --- | --- | --- |
| `run_status` | `finished`, `awaiting_input`, `cancelled`, `error` | Why execution stopped |
| `task_outcome` | `success`, `partial`, `blocked`, `unknown` | Whether the runtime delivered a result without a known execution blocker |
| `audit_status` | `not_run`, `passed`, `warning`, `unknown` | Advisory semantic-review result; it does not control task termination |

`completed` remains as a compatibility field and is `true` only when `task_outcome` is `success`. A timeout, exhausted retry budget, cancellation, required confirmation, or another condition that actually stops execution must never be presented as completed. Historical tool failures do not independently determine the outcome after the executor has continued and produced an answer. When a run really stops but useful work exists, Praxis returns it as a clearly labelled partial result with the unresolved gaps and a resumable checkpoint. An enabled audit always uses `completion_mode=audited`; `audit_status` is diagnostic only and never means that Praxis has verified the answer. External Eval owns semantic correctness.

## Context and parallel work

As a session approaches the model context limit, Praxis compacts older material while retaining the goal, evidence, failures, and recent interaction. Independent read-only calls may run in parallel; stateful or high-risk actions remain ordered and can require confirmation.

These deterministic mechanics belong in regular tests. End-to-end task quality, stability, latency, tool-call count, and token use belong in the repeated [Eval suite](evaluation.md).
