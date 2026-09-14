# Praxis v0.5.0 Release Notes

Release date: September 14, 2026

Praxis v0.5.0 expands datasource and observability workflows, unifies object
building on the Agent runtime, and improves the reliability and readability of
interactive Chat.

## Highlights

### Generic HTTP Services and Prometheus correlation

- Added generic HTTP Service registration with stored authentication, health
  checks, advanced headers, and knowledge-base bindings.
- Added a Prometheus-aware read contract for focused readiness, instant query,
  range query, alert, and active-target requests.
- Added canonical MySQL exporter metric mappings and tighter evidence rules for
  connection-pressure diagnosis.
- Added a complete local observability demo with Praxis, MySQL 8.4, MySQL
  Exporter, Prometheus, seeded integrations, and a connection-pressure workload.

### Datasource access and safer automation

- Added datasource access levels so user and administrative connections can be
  routed explicitly within the same database cluster.
- Added an optional AI action-confirmation bypass for controlled environments;
  the safer confirmation-first behavior remains the default.
- Continued Agent workflows correctly after approved actions.

### Unified Agent and Chat execution

- Routed Page and Function builds through their domain Chat Agents and
  consolidated coding loops on the reasoning engine.
- Stabilized context-usage reporting, history ordering, and streamed reasoning
  narration.
- Bounded large tool results in model history while preserving the complete tool
  result event for the UI and audit trail.
- Hid unsupported regeneration and editing controls, kept hover actions out of
  message layout, and limited smooth scrolling to explicit scroll-to-bottom
  actions.

## Compatibility and upgrade notes

- The package and local Docker build version is now synchronized with the
  release tag at `0.5.0`.
- Database migration `0005_generic_http_services` adds generic Service
  credentials and knowledge bindings.
- Database migration `0006_datasource_access_level` adds datasource access
  levels.
- Existing installations should keep `SECRET_KEY` stable and run the normal
  startup migration path before serving traffic.
- The observability Compose project is a local demonstration environment and
  must not be used as a production deployment template.

## Validation summary

- Complete non-LLM backend suite: 578 passed, 4 skipped, 7 deselected.
- Focused backend and API lifecycle E2E suite: 124 passed, 2 skipped.
- Frontend suite: 139 passed across 21 test files.
- Frontend TypeScript production build passed.
- Ruff lint and format checks, repository hygiene, and changed-file frontend
  lint passed.
- A clean SQLite database migrated from the initial schema through
  `0006_datasource_access_level`.
- A real Uvicorn process served the frontend and health endpoint, completed
  onboarding against an isolated OpenAI-compatible test endpoint, streamed the
  expected Chat SSE response, and persisted the user message, assistant message,
  context events, task state, and completion event.

The local release environment did not provide a Docker client, so the
multi-architecture image build and push are delegated to the tag-triggered
GitHub Actions workflow. No new live-model DBA Eval result is claimed for this
release.
