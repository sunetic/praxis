# Praxis Documentation

Praxis is a self-hosted **AI-native database agent platform**. It brings database connections, natural-language conversations, domain knowledge, reusable agents, functions, and scheduled tasks into one workspace, so one-off database work can evolve into repeatable capabilities.

Praxis currently supports MySQL and PostgreSQL and can connect to OpenAI-compatible model services.

## Where to start

| If you want to… | Read this |
| --- | --- |
| Understand what Praxis is designed to solve | [Positioning and design philosophy](concepts/philosophy.md) |
| Run Praxis locally | [Quickstart](getting-started/quickstart.md) |
| Complete your first database conversation | [Datasources](features/datasources.md) → [Chat](features/chat.md) |
| Save a successful workflow for reuse | [Agent](features/agents.md) and [Skills](features/skills.md) |
| Run tasks automatically | [Function](features/functions.md) and [Scheduler](features/schedulers.md) |
| Understand long-task reliability | [Long-task reliability](reliability/long-tasks.md) and [Observability](reliability/observability.md) |
| Compare releases or models using real tasks | [Evaluation](reliability/evaluation.md) |

## Documentation scope

This site contains public product documentation for users and contributors: product context, design principles, workflows, reliability mechanisms, and public boundaries. For exact API request schemas, refer to `/docs` (OpenAPI) on a running Praxis instance.

Internal requirement drafts, engineering orchestration workflows, and implementation notes are not public documentation and are excluded from both navigation and published artifacts.
