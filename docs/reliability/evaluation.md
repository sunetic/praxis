# Evaluation

Praxis separates deterministic tests from real-model evaluation:

- `make test` verifies deterministic code behavior and can run reliably on every PR and in CI.
- `make eval` uses local model credentials and the real service path to evaluate long-task quality. It does not currently run in GitHub Actions.

This boundary keeps LLM keys out of the public repository and prevents provider limits, cost, and sampling variance from making every PR unstable.

## Prerequisites

- Run `make install`.
- Docker must be available locally. A suite may pull its fixture image on the first run.
- Configure a working OpenAI-compatible model in local Praxis settings or environment variables.

The runner prefers model settings from the local Praxis management database. The API key is passed only to the isolated backend process for the run and is not written to reports.

## Common commands

```bash
make eval                         # Run the complete default suite
make eval-list                    # List available cases
make eval EVAL_CASE=C03           # Run one case
make eval EVAL_REPEAT=3           # Repeat each case three times
make eval EVAL_BASELINE=path/to/summary.json
```

Use the lower-level command for more control:

```bash
uv run python -m evals.pg_dba.run --help
uv run python -m evals.pg_dba.run --case C10 --case-timeout 1200
uv run python -m evals.pg_dba.run --output /tmp/praxis-eval-candidate
```

`--output` must point to a directory that does not yet exist, preventing evidence from separate runs from being mixed.

## Command options

| Option | Default | Purpose |
| --- | --- | --- |
| `--case` | `all` | Run all cases or one selected case |
| `--repeat` | `1` | Attempts per case |
| `--output` | Timestamped directory | Select a new artifact directory |
| `--baseline` | None | Compare with an earlier `summary.json` |
| `--settings-db` | Default local management database | Read another model configuration |
| `--postgres-image` | `postgres:16-alpine` | PostgreSQL fixture image |
| `--workload-repeats` | `8` | Statistical workload generation rounds |
| `--case-timeout` | `900` seconds | Timeout per case |
| `--case-delay` | `3` seconds | Delay between cases; increase for rate limits |
| `--list-cases` | Disabled | List cases without starting the environment |

## What Eval should answer

Eval supports two decisions:

1. Did a change to the Agent loop, prompt, context handling, or runtime make Praxis worse at long tasks?
2. Which model is the better Praxis execution model on the same real tasks?

A useful report therefore needs more than a single pass rate:

| Dimension | Meaning |
| --- | --- |
| Task pass rate | Percentage that meets completion criteria and every hard gate |
| Reliability | Whether the real HTTP/streaming run completes and persists a valid answer |
| Evidence-grounded intelligence | Whether the run obtains and uses required database or knowledge evidence |
| Safety | Whether a prohibited change occurred; this is a hard gate and cannot be offset by averages |
| Provider availability | Whether model connection, rate limiting, and transport complete; separates provider problems from product problems |
| Efficiency | Duration and counts of LLM and tool calls; cost can be calculated from provider pricing |

## When to run Eval

- Before a release.
- After changing the Agent loop, prompt, Skill selection, context compaction, tools, or safety policy.
- When changing models or providers.
- Periodically to establish a trend baseline.
- After a long-task failure that is difficult to reproduce in production.

## Recommended comparison method

1. Run the complete suite on a reference commit and reference model, and retain `summary.json`.
2. Run the candidate commit or model with the same suite, case versions, and repeat count.
3. Inspect safety failures first, infrastructure/provider failures second, and task quality last.
4. Repeat close results at least three times so one sample does not decide model selection.
5. Inspect raw evidence for failed cases to determine whether the cause is environment, execution, scoring, or model reasoning.

Only compare reports with the same suite version and fixtures. When prompts, test data, or pass criteria change, increment the suite version and establish a new baseline.

## Report format

Each run produces a readable `report.md`, a machine-readable `summary.json`, per-case evidence, and runtime logs. For a release decision, use one comparison table with commit, model, suite version, repeat count, pass rate, reliability, intelligence, safety, and provider availability; link disputed results to their case evidence.

Each attempt is classified as `passed`, `quality_fail`, `infra_fail`, `incomplete`, or `safety_fail`. HTTP 200 alone is not a pass: the run must complete, persist an answer, and meet evidence thresholds. Any prohibited database change causes a safety failure.

| Exit code | Meaning |
| --- | --- |
| `0` | Every selected attempt passed |
| `1` | A quality threshold failed |
| `2` | Environment, provider, transport, or execution did not complete |
| `3` | A safety gate failed |

When a report shows `Pass rate 0%`, inspect the classifications first. All `infra_fail` usually means provider connectivity or rate limiting and does not prove weak model reasoning. `quality_fail` means the run completed but did not meet evidence requirements. Any `safety_fail` should immediately block the candidate.

The default output is `.artifacts/evals/<UTC timestamp>/`:

```text
report.md
summary.json
evidence/C03-attempt-1.json
backend.log
fixture.log
runtime/
```

These directories are excluded from commits by default. Before sharing a report, still check its logs and answers for real credentials or sensitive data.

See [PostgreSQL DBA Cases](pg-dba-eval.md) for the built-in case catalog.
