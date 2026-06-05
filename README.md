<p align="center">
  <img src="assets/logo-banner.png" alt="Praxis" width="480">
</p>

<h4 align="center">
  <a href="#quick-start">Quick Start</a> |
  <a href="#features">Features</a> |
  <a href="https://github.com/nicholasgasior/praxis-ce/issues">Issues</a> |
  <a href="#contributing">Contributing</a>
</h4>

<p align="center">
  <a href="https://github.com/nicholasgasior/praxis-ce/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.11+-yellow?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/MySQL-supported-4479A1?logo=mysql&logoColor=white" alt="MySQL">
  <img src="https://img.shields.io/badge/PostgreSQL-supported-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white" alt="Docker">
</p>

<p align="center">
  <b>AI-native database agent platform.</b><br>
  Turn your databases into autonomous AI workspaces — chat, analyze, automate, all in natural language.
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README_CN.md">中文</a>
</p>

---

<p align="center"><img src="assets/demo.gif" alt="Praxis Demo" width="720"></p>

## What Is Praxis

Praxis is a self-hosted platform that turns your databases into conversational AI workspaces. Instead of writing SQL by hand, you describe what you need in plain language — Praxis agents understand your schema, write and execute queries, analyze results, and even schedule recurring tasks.

It works with any **OpenAI-compatible** model provider (OpenAI, local Ollama, etc.) and supports **MySQL** and **PostgreSQL** out of the box.

## Quick Start

### Docker (recommended)

```bash
docker run -d -p 8000:8000 -v ~/.praxis/data:/app/data sunzy2/praxis:latest
```

Open [http://localhost:8000](http://localhost:8000) and follow the onboarding wizard to connect your first database.

### Docker Compose

```yaml
# docker-compose.yml
version: '3.8'
services:
  praxis:
    image: sunzy2/praxis:latest
    ports:
      - "8000:8000"
    volumes:
      - praxis_data:/app/data
    restart: unless-stopped
volumes:
  praxis_data:
```

```bash
docker compose up -d
```

### Configuration

Copy `.env.example` to `.env` and configure your AI provider:

```bash
# Use OpenAI
AI_BASE_URL=https://api.openai.com/v1
AI_API_KEY=sk-your-key
AI_MODEL=gpt-4

# Or use a local model (e.g. Ollama)
AI_BASE_URL=http://localhost:11434/v1
AI_MODEL=llama3
```

## Features

### Chat with Your Database

Talk to your database in natural language. Ask questions, get SQL + results, iterate in context. The agent writes, explains, and executes queries against your live datasources.

### Custom Agents

Create purpose-built agents with specific prompts, tools, skills, and datasource bindings. Tailor each agent for a domain — DBA diagnosis, business reporting, data QA, and more.

### Pluggable Skills

Skills are Markdown-based prompt modules that give agents domain expertise (e.g. layered diagnosis, slow-query analysis). Write your own with YAML front matter — no code required.

### SQL Analysis

Paste a SQL statement and get execution plan analysis, rewrite suggestions, and AI-powered performance insights.

### Knowledge Base

Build knowledge bases from documents. Agents reference them during conversations for context-aware, grounded answers.

### Functions & Scheduling

Define reusable data-retrieval functions backed by SQL templates. Build and test them visually, then schedule on cron or interval for automated reporting and monitoring.

### Channels

Connect external messaging platforms (Slack, DingTalk, etc.) so users can interact with agents outside the Praxis UI.

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

- [GitHub Issues](https://github.com/nicholasgasior/praxis-ce/issues) — Bug reports & feature requests
- [GitHub Discussions](https://github.com/nicholasgasior/praxis-ce/discussions) — Questions & ideas

## License

Praxis Community Edition is open source. See [LICENSE](LICENSE) for details.
