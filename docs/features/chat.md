# Chat

Chat is the primary Praxis workspace. It is not a single-turn Q&A box: it can select datasources, call database tools, use Skills and knowledge bases, handle confirmations, and save reusable results.

## Good uses for Chat

- Database health checks, incident investigation, and SQL diagnosis.
- Complex tasks that collect several kinds of evidence before reaching a conclusion.
- Exploring a new working method, then saving it as an Agent after it stabilizes.
- Comparing live evidence with team standards, runbooks, or incident-severity rules from a knowledge base.

## A reliable conversation pattern

1. Select the correct datasource and Agent.
2. State the goal, time range, output requirements, and prohibited actions.
3. Ask for evidence first and require facts, inferences, and recommendations to be distinguished.
4. Inspect tool results. For high-risk confirmation cards, verify the target and impact before approval.
5. Save the workflow as an Agent only after it is stable and reusable.

## Long-task behavior

Complex tasks may involve multiple tool calls, retries, and verification steps. Praxis maintains task constraints and execution evidence, compacting older context as it approaches the configured limit. Process events explain ongoing work; the final answer should summarize verified conclusions and unresolved issues.

Provider rate limits or network failures do not automatically indicate poor reasoning. If a run stops, first retry or inspect the model service before judging the plan. Use [Eval](../reliability/evaluation.md) for batch comparisons instead of drawing a model conclusion from one chat.

## Safety

Use read-only accounts by default. Confirmation cards are part of the safety boundary for SQL writes, session termination, and platform-object deletion; never approve one without verifying the actual target.
