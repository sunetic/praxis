# Praxis v0.6.0 Release Notes

Release date: September 23, 2026

Praxis v0.6.0 replaces the legacy Chat and build orchestration stacks with one
durable Pydantic AI runtime shared by Chat, Scheduler, Function, Page, Skill,
knowledge, datasource, and external-service workflows.

## Highlights

### One durable Agent runtime

- Added persistent conversations, native model messages, ordered run events,
  tool-call records, model snapshots, context snapshots, and explicit resource
  budgets.
- Unified streaming, cancellation, approval, interruption recovery, and
  continuation across interactive and scheduled runs.
- Removed the custom planning, reflection, task-contract, completion-repair,
  and outer retry loops. The main Agent now chooses its next step from the
  conversation and actual tool results.

### Safer external effects

- Added typed, server-authorized tools for datasource reads and approved writes,
  external services, knowledge, and versioned Function, Page, and Skill
  workspaces.
- Database read tools accept exactly one read-only statement per call and expose
  that constraint directly in the model-facing tool schema and actionable
  errors.
- Mutating calls retain stable identities across approval and recovery. Calls
  whose external outcome cannot be proven are stopped for explicit
  reconciliation instead of being replayed automatically.

### Native product workflows

- Migrated Function, Page, Skill, Scheduler, and custom-Agent experiences to the
  shared conversation and run protocol.
- Added versioned artifact editing, validation, publication checks, isolated
  Function execution, and browser-based Page validation.
- Added context-capacity status, native compaction records, resilient event
  replay, and refresh-safe conversation rendering.

## Compatibility and upgrade notes

- The package version is `0.6.0` and the release tag is `v0.6.0`.
- This release establishes a clean initial schema and intentionally removes the
  legacy runtime tables, APIs, migrations, and compatibility switches. Existing
  pre-0.6 runtime conversations and build records are not migrated in place;
  deploy with a fresh application database or perform an explicit export and
  migration outside the application.
- Keep datasource encryption keys stable when carrying datasource credentials
  into a new database. Do not copy plaintext secrets into release artifacts.
- The frontend production bundle still emits the existing large-chunk warning;
  code splitting remains follow-up work.

## Validation

- Complete backend suite: 665 passed, 2 skipped.
- Isolated Agent Runtime contract suite: 368 passed, 2 skipped.
- Complete frontend suite: 112 passed across 20 test files.
- Production TypeScript and Vite build passed.
- Ruff lint and formatting, repository hygiene, i18n regression guard, and diff
  whitespace checks passed.
- A real Chromium and configured model completed a three-turn conversation,
  preserved history across reloads, executed the authorized read-only query
  `SELECT 13 AS browser_probe`, and observed the returned value `13` without
  browser errors.

Project-wide ESLint still reports the pre-existing explicit-`any`, Fast Refresh,
and Hook-rule debt. The release does not expand the i18n debt baseline.
