# Long-task Reliability

The hard part of a long or complex task is not whether a model can produce one good answer. It is whether the system keeps the goal across many tool calls, handles failures, verifies evidence, and stops only when the work is actually complete.

Praxis strengthens these tasks in several ways.

## Task constraints

Complex requests are organized into executable goals and completion criteria. Later steps stay aligned with those constraints instead of completing only the easiest part after the conversation grows long.

## Evidence-driven work

Agent conclusions should be grounded in tool results, live database state, or knowledge-base material. Preserved evidence supports coverage checks and final verification instead of relying only on the model's claim that the task is complete.

## Failure recovery

Temporary rate limits, connection failures, and no-progress loops need different responses. Recoverable failures can use backoff and retry; repeated lack of progress should stop consuming resources and expose the problem; retries must never bypass confirmation for high-risk actions.

## Context management

As a session approaches the model's context limit, Praxis compacts older material while trying to retain the goal, key facts, open items, and recent interaction. The context window and compaction threshold are configurable, but changes should be verified with long-task Eval.

## Parallel work and verification

Independent read-only investigations can run in parallel to reduce elapsed time. Stateful or high-risk actions remain ordered. Final verification focuses on completion criteria and evidence gaps, not answer length.

These mechanisms improve the success rate but cannot eliminate variation from the model, provider, or live database. Use the same [Eval suite](evaluation.md) repeatedly for release regression and model selection.
