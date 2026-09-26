# Praxis v0.6.1 Release Notes

Release date: September 26, 2026

Praxis v0.6.1 hardens the unified Agent runtime after the v0.6.0 redesign and
completes the local observability demo experience.

## Fixed

- Restored the global confirmation bypass setting so every approval-gated
  action can execute directly when the operator explicitly enables it.
- Model responses that repeatedly fail tool-argument validation are now
  classified as unstable output and stopped before the unresolved action runs.
- Chat now explains that proactive safety stop in the selected interface
  language instead of exposing an internal cleanup message or error code.
- Datasource revocation is honored by existing conversation context.

## Maintenance

- Split Agent persistence and frontend page-model responsibilities into smaller
  modules and tightened runtime type, lint, and repository quality boundaries.
- Strengthened frontend loading, locale, and release-quality checks.
- Added cAdvisor to the Compose demo for container-level monitoring.

## Compatibility and upgrade notes

- The package version is `0.6.1` and the release tag is `v0.6.1`.
- No schema reset is required when upgrading from v0.6.0.

## Validation

- Complete backend suite: 672 passed, 2 skipped.
- Complete frontend suite: 115 passed across 20 test files.
- Production TypeScript and Vite build passed.
- Ruff lint and formatting, i18n regression guard, and diff whitespace checks passed.
