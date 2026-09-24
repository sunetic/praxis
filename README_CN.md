<p align="center">
  <img src="assets/logo-banner.svg" alt="Praxis" width="300">
</p>

<p align="center">
  <b>AI 原生数据库 Agent 平台。</b><br>
  用自然语言操作数据库，把有效的处理流程沉淀为 Agent 并自动运行。
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

## 功能

Praxis 是面向数据库工作的 AI Agent 平台。它让 Agent 理解数据库结构和运行状态，通过对话完成数据查询与分析、问题诊断和变更操作，并将有效的处理过程沉淀为可复用、可定时执行的 Agent。

- **对话式数据库操作**：在 Chat 中查看表结构、查询数据、诊断问题或申请数据变更。Agent 会调用工具，并根据返回结果继续分析。
- **可复用 Agent**：把验证有效的工作流程保存为 Agent，随时基于最新数据再次运行。
- **定时自动化**：按计划运行 Agent，并保存每次执行结果。
- **函数**：将带参数的 SQL 查询封装为可复用、可测试的函数。
- **知识与技能**：为 Agent 提供工作时需要参考的文档和领域指令。

### 诊断数据库

<p align="center"><img src="assets/demo-chat.gif" alt="数据库健康检查" width="720"></p>

### 保存并运行 Agent

<p align="center"><img src="assets/demo-agent.gif" alt="保存和运行 Agent" width="720"></p>

### 定时执行任务

<p align="center"><img src="assets/demo-scheduler.gif" alt="调度 Agent" width="720"></p>

## 快速开始

### 使用 Docker 单独启动 Praxis

已经有数据库可供连接时，可以直接运行 Praxis：

```bash
docker run -d \
  --name praxis \
  -p 8000:8000 \
  -v praxis_data:/app/data \
  sunzy2/praxis:latest
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)，根据引导配置模型提供商并添加数据源。

### 使用 Docker Compose 启动完整演示环境

演示环境包括 Praxis、MySQL、MySQL Exporter、Prometheus、模拟负载，以及预先配置好的数据源和服务连接。

```bash
git clone https://github.com/sunetic/praxis.git
cd praxis
docker compose run --build --rm demo-init
```

初始化完成后，打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)，只需配置模型提供商。演示服务地址如下：

- Praxis：`http://127.0.0.1:8000`
- MySQL：`127.0.0.1:3308`（用户名 `app`，密码 `praxis-demo-app`，数据库 `app`）
- Prometheus：`http://127.0.0.1:9090`
- MySQL Exporter：`http://127.0.0.1:9104/metrics`

使用 `docker compose down` 停止环境。需要同时清除演示数据时，使用 `docker compose down --volumes`。

## 执行 Eval

真实模型 Eval 会使用隔离的 PostgreSQL 或 MySQL 测试环境，执行完整的 Chat 流程。开始前请安装项目依赖、启动 Docker，并在 Praxis 设置中配置好模型和凭据。

```bash
uv sync

make eval                                  # PostgreSQL Eval
make eval EVAL_SUITE=mysql                 # MySQL Eval
make eval EVAL_SUITE=mysql EVAL_CASE=M03   # 只运行一个 case
make eval EVAL_PROFILE=model               # 使用固定 harness 对比模型
make eval-list                             # 查看当前 suite 的 case
```

可通过 `EVAL_REPEAT=<次数>` 重复执行，通过 `EVAL_OUTPUT=<路径>` 指定报告位置。报告默认写入 `.artifacts/evals/`。关于 case、评分方式和报告解读，参见 [Eval 文档](https://sunetic.github.io/praxis/zh/reliability/evaluation/)。
