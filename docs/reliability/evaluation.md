# Evaluation

Praxis separates deterministic tests from real-model evaluation:

- `make test` verifies deterministic mechanics such as task-state transitions, retry limits, checkpoints, resume, parsing, and safety guards. It can run reliably on every PR and in CI.
- `make eval` uses local model credentials and isolated database fixtures to evaluate nondeterministic task outcomes. It does not currently run in GitHub Actions.

This boundary keeps LLM keys out of the public repository and prevents provider limits, cost, and sampling variance from making every PR unstable.

## Prerequisites

- Run `make install`.
- Docker must be available locally. A suite may pull its fixture image on the first run.
- Configure a working OpenAI-compatible model in local Praxis settings or environment variables.

The runner prefers model settings from the local Praxis management database. The API key stays in local process memory and is not written to cases or reports.

## Common commands

```bash
make eval                                      # Run all PostgreSQL cases
make eval-list                                 # List PostgreSQL cases
make eval EVAL_SUITE=mysql                     # Run all MySQL cases
make eval-list EVAL_SUITE=mysql                # List MySQL cases
make eval EVAL_SUITE=mysql EVAL_CASE=M03       # Run one MySQL case
make eval EVAL_SUITE=mysql EVAL_REPEAT=3       # Repeat each MySQL case
make eval EVAL_SUITE=mysql EVAL_PROFILE=model  # Compare models with the fixed harness
make eval EVAL_EXPECTED_MODEL=DeepSeek-V4-Flash-0731
make eval EVAL_BASELINE=path/to/summary.json
```

Use the lower-level command for more control:

```bash
uv run python -m evals.pg_dba.run --help
uv run python -m evals.pg_dba.run --case C10 --case-timeout 1200
uv run python -m evals.mysql_dba.run --help
uv run python -m evals.mysql_dba.run --case M10 --case-delay 10
uv run python -m evals.mysql_dba.run --profile model --case M03 --repeat 3
uv run python -m evals.mysql_dba.run --output /tmp/praxis-mysql-eval-candidate
```

`--output` must point to a directory that does not yet exist, preventing evidence from separate runs from being mixed.

A complete live suite runs cases sequentially so they share one stable fixture without interfering with each other's database sessions. Its duration is therefore approximately the sum of the Chat executions: ten five-minute cases take about fifty minutes, while fixture startup, scoring, and report generation are normally a small fraction of the run. The exact time can still range from tens of minutes to several hours depending on the model's tool and verification loops, and may consume substantial tokens. Start with `--case` while developing; use the complete suite for release/model decisions and retain its report. Do not shorten the timeout only for one candidate when comparing models.

## Command options

| Option | Default | Purpose |
| --- | --- | --- |
| `--case` | `all` | Run all cases or one selected case |
| `--repeat` | `1` | Attempts per case |
| `--output` | Timestamped directory | Select a new artifact directory |
| `--baseline` | None | Compare with an earlier `summary.json` |
| `--settings-db` | Default local management database | Read another model configuration |
| `--expected-model` | None | Abort before provider access unless the resolved model matches exactly |
| `--postgres-image` | `postgres:16-alpine` | PostgreSQL suite fixture image |
| `--mysql-image` | `mysql:8.4` | MySQL suite fixture image |
| `--workload-repeats` | `8` | Statistical workload generation rounds |
| `--case-timeout` | `300` seconds | Total execution deadline per case, not a per-request or idle-read timeout |
| `--case-delay` | `3` seconds | Delay between cases; increase for rate limits |
| `--profile` | `praxis` | `praxis` evaluates the product path; `model` uses the fixed comparison harness |
| `--max-tool-rounds` | `20` | Tool-round cap for the fixed model harness |
| `--list-cases` | Disabled | List cases without starting the environment |

## Purpose, mechanism, and boundary

Eval supports two decisions:

1. Did a change to the Agent loop, prompt, context handling, or runtime make Praxis worse at long and complex tasks?
2. Which model is the better Praxis execution model on the same real tasks?

An Eval case contains a task, an isolated environment, reference facts or a target state, optional authoritative-source requirements, and graders. The runner gives the task to the selected profile, observes the final answer and externally visible state, applies correctness and safety checks, and records the execution trajectory separately. Repeated attempts measure stability rather than assuming that one sample represents the model.

Eval grades **what was achieved**, not **how the system chose to achieve it**. It does not prescribe the internal plan, tool choice, tool order, knowledge-base use, or a minimum number of calls. Rules such as `minimum_tool_calls`, `minimum_sql_calls`, or “must call the knowledge base” are invalid Eval pass conditions: a capable model may already know a public fact or may reach the same correct result through a different valid path.

There are two deliberate exceptions to pure answer-text comparison:

- Safety may inspect the real environment for prohibited mutations or side effects. A plausible answer cannot hide unsafe execution.
- A case may require evidence when its answer depends on private, local, or explicitly authoritative information. The grader checks that the relevant claim is supported, not that a particular retrieval tool was called.

Deterministic implementation mechanics—state transitions, checkpoints, resume behavior, retry caps, parsing, tool protocol, and verifier control flow—belong in tests. Provider connectivity and rate limits are classified as infrastructure availability. They are reported by Eval so a failed provider is not mistaken for weak reasoning, but they are not task-quality criteria.

Comparison is ordered, not collapsed into a single process score:

1. Safety is a hard gate.
2. Correctness, completeness, required evidence, and answer quality determine whether the task passed.
3. Repeated attempts determine reliability.
4. Only among candidates with comparable result quality and reliability, fewer tool calls, fewer failed calls or retries, lower latency, and lower token consumption indicate better efficiency.

Process metrics can explain or break a tie, but they cannot rescue a wrong answer or fail a correct answer merely because it used an unexpected path.

A useful report therefore needs more than a single pass rate:

| Dimension | Meaning |
| --- | --- |
| Task pass rate | Percentage that meets completion criteria and every hard gate |
| Reliable case rate | Percentage of cases that pass every repeated trial |
| Reliability | Whether the selected run path completes and persists a valid answer |
| Task outcome | Coverage of reference facts and target state; the primary correctness score |
| Answer quality | Risk ordering, uncertainty, tradeoffs, and other scenario-specific criteria |
| Required evidence | Whether source-specific claims are supported when a case names an authoritative source |
| Safety | Whether a prohibited change occurred; this is a hard gate and cannot be offset by averages |
| Provider availability | Whether model connection, rate limiting, and transport complete; separates provider problems from product problems |
| Trajectory diagnostics | Duration, tokens, tool calls, failed calls, retries, and verifier attempts; explanatory, not prescribed steps |

Use `praxis` for regression decisions: it evaluates the model and Praxis Agent harness together through the real backend and Chat path. Use `model` for model selection: it fixes a small OpenAI-compatible tool loop and changes only the candidate model. Both profiles share cases, fixtures, reference answers, safety gates, and report schema; compare baselines only within the same profile.

## When to run Eval

- Before a release.
- After changing the Agent loop, prompt, Skill selection, context compaction, tools, or safety policy.
- When changing models or providers.
- Periodically to establish a trend baseline.
- After a long-task failure that is difficult to reproduce in production.

## Recommended comparison method

1. Choose the decision first: `praxis` for product regression or `model` for model selection.
2. Run the complete suite on a reference commit and reference model, and retain `summary.json`.
3. Run the candidate with the same profile, suite, case versions, timeout, and repeat count.
4. Inspect safety failures first, infrastructure/provider failures second, and task outcome last.
5. Repeat close results at least three times so one sample does not decide model selection.
6. Inspect raw evidence and trajectory diagnostics to locate environment, execution, scoring, harness, or model failures.

Only compare reports with the same suite version and fixtures. When prompts, test data, or pass criteria change, increment the suite version and establish a new baseline.

For formal runs, set `EVAL_EXPECTED_MODEL`. This prevents an unavailable or empty default local settings database from silently selecting a different `.env` model. An explicitly supplied `--settings-db` fails closed when it is missing or unreadable.

## Report format

Each run produces a readable `report.md`, a machine-readable `summary.json`, per-case evidence, and runtime logs. Compare profile, commit, model, suite version, repeat count, pass rate, reliable case rate, task outcome, answer quality, required evidence, safety, and provider availability; link disputed results to their case evidence.

Each attempt is classified as `passed`, `quality_fail`, `infra_fail`, `incomplete`, or `safety_fail`. A run must complete, produce an answer, meet outcome/quality thresholds, satisfy case-specific evidence requirements, and pass the safety gate. Any prohibited database change causes a safety failure.

| Exit code | Meaning |
| --- | --- |
| `0` | Every selected attempt passed |
| `1` | A quality threshold failed |
| `2` | Environment, provider, transport, or execution did not complete |
| `3` | A safety gate failed |

When a report shows `Pass rate 0%`, inspect the classifications first. All `infra_fail` usually means provider connectivity or rate limiting and does not prove weak model reasoning. `quality_fail` means the run completed but missed an outcome, answer-quality, or case-specific evidence threshold. Any `safety_fail` should immediately block the candidate.

PostgreSQL output defaults to `.artifacts/evals/<UTC timestamp>/`; MySQL output defaults to `.artifacts/evals/mysql/<UTC timestamp>/`:

```text
report.md
summary.json
evidence/C03-attempt-1.json
backend.log                 # praxis profile only
fixture.log
runtime/
```

These directories are excluded from commits by default. Before sharing a report, still check its logs and answers for real credentials or sensitive data.

See [PostgreSQL DBA Cases](pg-dba-eval.md) and [MySQL DBA Cases](mysql-dba-eval.md) for the built-in catalogs.
