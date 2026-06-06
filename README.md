<p align="center">
  <img src="assets/logo-banner.svg" alt="Praxis" width="360">
</p>

<p align="center">
  <b>AI-native database agent platform.</b><br>
  Turn your databases into autonomous AI workspaces — chat, analyze, automate, all in natural language.
</p>

<p align="center">
  <a href="https://github.com/sunetic/praxis/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.11+-yellow?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/MySQL-supported-4479A1?logo=mysql&logoColor=white" alt="MySQL">
  <img src="https://img.shields.io/badge/PostgreSQL-supported-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white" alt="Docker">
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README_CN.md">中文</a>
</p>

---

## What Is Praxis

Praxis is a self-hosted platform that turns your databases into conversational AI workspaces. Instead of writing SQL by hand, you describe what you need in plain language — Praxis agents understand your schema, write and execute queries, analyze results, and schedule recurring tasks. It works with any **OpenAI-compatible** model provider and supports **MySQL** and **PostgreSQL** out of the box.

## See It in Action

### Diagnose Your Database

Ask the agent to run a health check. It autonomously examines table sizes, index usage, and storage metrics — running multiple diagnostic queries and presenting findings with actionable recommendations, just like a DBA would.

<p align="center"><img src="assets/demo-chat.gif" alt="Database health check" width="720"></p>

### Save & Run Agents

When a diagnostic workflow works well, save it as a reusable **Agent** with one command. The agent captures the entire multi-step analysis — run it anytime against any datasource to repeat the same checks with fresh data.

<p align="center"><img src="assets/demo-agent.gif" alt="Save and run agents" width="720"></p>

### Automate with Scheduler

Schedule any agent for recurring execution — daily health checks, weekly index reviews, periodic performance audits. Praxis runs them automatically and stores the results.

<p align="center"><img src="assets/demo-scheduler.gif" alt="Schedule agents" width="720"></p>

## Quick Start

```bash
docker run -d -p 8000:8000 -v ~/.praxis/data:/app/data sunzy2/praxis:latest
```

Open [http://localhost:8000](http://localhost:8000) and follow the onboarding wizard to connect your first database and configure your AI provider.

## More Features

- **Pluggable Skills** — Markdown-based prompt modules that give agents domain expertise (e.g. layered diagnosis, slow-query analysis). Write your own with YAML front matter.
- **Knowledge Base** — Upload documents for agents to reference during conversations, providing context-aware, grounded answers.
- **Functions** — Define reusable data-retrieval functions backed by SQL templates. Build and test them visually, then expose to agents or schedule for automation.
- **Channels** — Connect external messaging platforms (Slack, DingTalk, etc.) so users can interact with agents outside the Praxis UI.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Praxis UI                          │
│                  (React + TypeScript)                    │
└──────────────────────┬──────────────────────────────────┘
                       │ REST API
┌──────────────────────▼──────────────────────────────────┐
│                  Praxis Backend                         │
│               (FastAPI + Python 3.11+)                  │
│                                                         │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌───────────┐ │
│  │  Agents  │ │  Skills  │ │ Functions │ │ Scheduler │ │
│  └────┬─────┘ └────┬─────┘ └─────┬─────┘ └─────┬─────┘ │
│       └─────┬──────┴─────────────┘              │       │
│             ▼                                   │       │
│  ┌───────────────────┐  ┌────────────────────┐  │       │
│  │   LLM Provider    │  │    Datasources     │◄─┘       │
│  │ (OpenAI-compat.)  │  │  MySQL/PostgreSQL  │          │
│  └───────────────────┘  └────────────────────┘          │
└─────────────────────────────────────────────────────────┘
```

## Development

### Prerequisites

- Python 3.11+
- Node.js 18+
- [uv](https://github.com/astral-sh/uv) (Python package manager)

### Backend

```bash
make install       # install dependencies (uv sync)
make migrate       # run database migrations
make dev           # start API server at :8000 with hot reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev        # Vite dev server at :5173
```

### Docker Build

```bash
make docker-build  # builds praxis:latest
```

### Running Tests

```bash
make test          # run pytest
make lint          # run ruff + semantic guard
```

## Roadmap

- [ ] More database support (Oracle, SQL Server, ClickHouse, ...)
- [ ] Built-in dashboard & visualization
- [ ] Multi-user & RBAC
- [ ] Plugin marketplace for community skills
- [ ] MCP (Model Context Protocol) integration

## Contributing

Contributions are welcome! Whether it's bug reports, feature requests, or pull requests — all contributions are appreciated.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Community

- [GitHub Issues](https://github.com/sunetic/praxis/issues) — Bug reports & feature requests
- [GitHub Discussions](https://github.com/sunetic/praxis/discussions) — Questions & ideas

## License

Praxis Community Edition is open source. See [LICENSE](LICENSE) for details.
