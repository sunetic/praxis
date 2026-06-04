# Praxis

[English](README.md) | [中文](README_CN.md)

**AI-native database agent platform.** Turn your databases into autonomous AI workspaces.

## Quick Start

```bash
docker run -d -p 8000:8000 -v ~/.praxis/data:/app/data sunzy2/praxis:latest
```

Open [http://localhost:8000](http://localhost:8000) and follow the onboarding wizard to connect your first database.

## What Is Praxis

Praxis is a self-hosted platform that turns your databases into conversational AI workspaces. Instead of writing SQL by hand, you describe what you need in plain language — Praxis agents understand your schema, write and execute queries, analyze results, and even schedule recurring tasks.

It supports **MySQL** and **PostgreSQL** out of the box.

## Features

**Chat** — Talk to your database. Ask questions, get SQL + results, iterate in context. The agent writes, explains, and executes queries against your live datasources.

**Agent** — Create custom agents with specific prompts, tools, skills, and datasource bindings. Each agent can be tailored for a domain (DBA diagnosis, business reporting, data QA, etc.).

**Skill** — Pluggable prompt modules that give agents domain expertise (e.g. layered diagnosis policy, slow-query analysis). Write your own as Markdown files with YAML front matter.

**SQL Analysis** — Paste a SQL statement and get execution plan analysis, rewrite suggestions, and performance insights powered by the AI agent.

**Knowledge** — Build knowledge bases from documents. Agents can reference them during conversations for context-aware answers.

**Datasource** — Register MySQL / PostgreSQL connections. Agents query them directly via built-in `execute_sql` and `explain_sql` tools.

**Function** — Define reusable data-retrieval functions backed by SQL templates. Build and test them visually, then expose to agents or schedule for automation.

**Scheduler** — Run functions on a cron or interval schedule. Automate recurring data collection, reporting, and monitoring tasks.

**Channel** — Connect external messaging platforms so users can interact with agents outside the Praxis UI.

## Development

```bash
# Backend
make install       # install dependencies (uv sync)
make migrate       # run database migrations
make dev           # start API server at :8000 with hot reload

# Frontend (separate terminal)
cd frontend
npm install
npm run dev        # Vite dev server at :5173
```

### Docker Build

```bash
docker build -t praxis:latest .
```

## License

See [LICENSE](LICENSE) for details.
