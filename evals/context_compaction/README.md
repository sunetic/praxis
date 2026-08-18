# Context compaction eval

This manual live-model suite measures whether persistent Chat compaction retains operational facts while removing obvious digressions and repetition.

It covers three failure-prone cases:

1. hard safety/configuration constraints mixed with unrelated conversation and repeated status;
2. later user corrections that must replace stale environment details;
3. failed tool attempts, corrected object names, exact result counts, and artifact identifiers.

For every recall question, the configured Praxis model answers once from the original transcript and once from the compacted context. The runner reports baseline and compacted fact recall, retention ratio, distractor leakage, required/forbidden memory terms, and token reduction.

Run all scenarios with the model configured in Praxis:

```bash
uv run python -m evals.context_compaction.run
```

Run one case or keep a JSON report:

```bash
uv run python -m evals.context_compaction.run --scenario latest_correction_wins
uv run python -m evals.context_compaction.run --output artifacts/context-compaction-eval.json
```

The command exits non-zero when a scenario fails. Scenarios are versioned in `scenarios.json` and can be extended without adding UI behavior.
