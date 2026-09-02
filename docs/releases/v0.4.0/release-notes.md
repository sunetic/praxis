# Praxis v0.4.0 Release Notes

Release date: September 2, 2026

Praxis v0.4.0 improves long-running Agent completion behavior, hardens credential handling, and introduces reproducible live-model database evaluation for release decisions.

## Highlights

### More natural long-task completion

- Simplified the long-running Agent loop so completion is driven by the model's evidence-based judgment instead of a rigid verifier retry sequence.
- Made completion audit feedback advisory and separated prior failure history from the current completion decision.
- Retained persistent evidence, bounded transient retries, no-progress protection, elapsed-time limits, and per-tool timeouts.
- Removed deprecated adversarial-verification and forced-finalization configuration paths.

These changes preserve operational bounds while avoiding mechanically repeated verification loops or abrupt verifier-driven termination.

### Credential and settings hardening

- API keys are no longer returned by the settings API; clients receive `ai_api_key_configured` instead.
- Sensitive platform settings are encrypted at rest and existing plaintext API keys are migrated on application startup.
- The legacy datasource connection-info endpoint now returns HTTP `410 Gone` instead of exposing decrypted connection credentials.

### Release-grade live-model evaluation

- Added isolated PostgreSQL 16 and MySQL 8.4 DBA suites with ten realistic cases each.
- Added outcome, answer-quality, evidence, safety, provider-availability, and trajectory reporting.
- Added separate `praxis` and fixed-harness `model` profiles for product regression and model comparison.
- Consolidated shared database Eval infrastructure under `evals/dba_core` while keeping database-specific fixtures separate.
- Added English and Chinese evaluation documentation; generated artifacts remain local and are excluded from commits.

See the [v0.4.0 Eval Report](eval-report.md) for the release-candidate results and limitations.

## Compatibility and upgrade notes

- `GET /api/v1/settings` no longer returns `ai_api_key`. Integrations should use `ai_api_key_configured` to determine whether a key exists and submit a replacement key only when it should change.
- `GET /api/v1/datasources/{datasource_id}/connect-info` has been removed and returns HTTP `410 Gone`. Integrations must not depend on retrieving stored datasource passwords.
- Keep `SECRET_KEY` stable across restarts. It protects encrypted platform settings; changing it without migrating stored values can make existing secrets unreadable.
- Existing plaintext platform API keys are encrypted automatically during the first application startup after upgrading.
- Deprecated Agent environment settings are ignored. Review `.env.example` for the supported long-task controls and defaults.
- No manual database schema migration is required for this release.

## Validation summary

- PostgreSQL DBA Eval: 9/10 cases passed; safety passed; provider availability was 100%.
- MySQL DBA Eval: 9/10 cases passed; safety passed; provider availability was 100%.
- The release candidate was evaluated through the real Praxis backend and Chat path using isolated database fixtures.
- The complete non-LLM test suite passed: 543 passed, 4 skipped, and 7 deselected.
- Ruff lint and format checks, repository hygiene, and the strict documentation build passed.
- A clean-state backend acceptance run created a previously missing SQLite parent directory, completed application startup, and passed representative settings, channel, datasource, and Live SQL boundary requests over real HTTP.

The two Eval misses are documented rather than hidden: PostgreSQL C03 needed safer response ordering, and MySQL M04 did not fully report all connection-pressure contributors. No prohibited database change occurred in either suite.
