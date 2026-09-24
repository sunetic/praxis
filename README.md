<p align="center">
  <img src="assets/logo-banner.svg" alt="Praxis" width="300">
</p>

<p align="center">
  <b>AI-native database agent platform.</b><br>
  Chat with databases, automate recurring work, and turn successful workflows into reusable agents.
</p>

<p align="center">
  <a href="https://github.com/sunetic/praxis/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License"></a>
  <img src="https://img.shields.io/badge/MySQL-supported-4479A1?logo=mysql&logoColor=white" alt="MySQL">
  <img src="https://img.shields.io/badge/PostgreSQL-supported-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white" alt="Docker">
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README_CN.md">中文</a>
</p>

## Features

Praxis is an AI agent platform for database work. Its agents understand database schemas and runtime state, then use that context to query and analyze data, diagnose problems, carry out changes, and turn successful workflows into reusable scheduled agents.

- **Conversational database operations** — inspect schemas, query data, diagnose problems, and request changes through Chat. The agent can use multiple tools and continue reasoning from their results.
- **Reusable agents** — save a proven workflow as an Agent and run it again against current data.
- **Scheduled automation** — run Agents on a schedule and keep their execution results.
- **Functions** — package parameterized SQL retrieval as reusable, testable functions.
- **Knowledge and skills** — provide documents and domain instructions that agents can use while working.

### Diagnose a database

<p align="center"><img src="assets/demo-chat.gif" alt="Database health check" width="720"></p>

### Save and run an Agent

<p align="center"><img src="assets/demo-agent.gif" alt="Save and run agents" width="720"></p>

### Schedule recurring work

<p align="center"><img src="assets/demo-scheduler.gif" alt="Schedule agents" width="720"></p>

## Quickstart

### Run Praxis with Docker

Use this when you already have a database to connect:

```bash
docker run -d \
  --name praxis \
  -p 8000:8000 \
  -v praxis_data:/app/data \
  sunzy2/praxis:latest
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), then configure a model provider and add your datasource during onboarding.

### Run the complete demo with Docker Compose

The demo includes Praxis, MySQL, MySQL Exporter, Prometheus, generated workload, and preconfigured datasource and service connections.

```bash
git clone https://github.com/sunetic/praxis.git
cd praxis
docker compose run --build --rm demo-init
```

When initialization finishes, open [http://127.0.0.1:8000](http://127.0.0.1:8000) and configure only the model provider. The demo services are available at:

- Praxis: `http://127.0.0.1:8000`
- MySQL: `127.0.0.1:3308` (`app` / `praxis-demo-app`, database `app`)
- Prometheus: `http://127.0.0.1:9090`
- MySQL Exporter: `http://127.0.0.1:9104/metrics`

Stop the demo with `docker compose down`. To remove its data as well, use `docker compose down --volumes`.

## Eval

Live-model Evals exercise the real Chat path against isolated PostgreSQL or MySQL fixtures. Install the project dependencies, keep Docker running, and configure the model and credentials in Praxis Settings before starting.

```bash
uv sync

make eval                                  # PostgreSQL suite
make eval EVAL_SUITE=mysql                 # MySQL suite
make eval EVAL_SUITE=mysql EVAL_CASE=M03   # One case
make eval EVAL_PROFILE=model               # Fixed-harness model comparison
make eval-list                             # List cases in the selected suite
```

Use `EVAL_REPEAT=<n>` to repeat cases and `EVAL_OUTPUT=<path>` to choose the report path. By default, reports are written under `.artifacts/evals/`. See the [Eval documentation](https://sunetic.github.io/praxis/reliability/evaluation/) for case design, scoring, and report interpretation.
