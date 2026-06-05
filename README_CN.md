<p align="center">
  <img src="assets/logo-banner.png" alt="Praxis" width="480">
</p>

<h4 align="center">
  <a href="#快速开始">快速开始</a> |
  <a href="#功能特性">功能特性</a> |
  <a href="https://github.com/nicholasgasior/praxis-ce/issues">问题反馈</a> |
  <a href="#参与贡献">参与贡献</a>
</h4>

<p align="center">
  <a href="https://github.com/nicholasgasior/praxis-ce/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.11+-yellow?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/MySQL-supported-4479A1?logo=mysql&logoColor=white" alt="MySQL">
  <img src="https://img.shields.io/badge/PostgreSQL-supported-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white" alt="Docker">
</p>

<p align="center">
  <b>AI 原生数据库 Agent 平台。</b><br>
  把你的数据库变成自治的 AI 工作空间——对话、分析、自动化，全部用自然语言完成。
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README_CN.md">中文</a>
</p>

---

<p align="center"><img src="assets/demo.gif" alt="Praxis Demo" width="720"></p>

## Praxis 是什么

Praxis 是一个可自部署的平台，把你的数据库变成 AI 对话工作空间。不用手写 SQL，用自然语言描述你的需求——Praxis Agent 理解你的表结构，自动编写和执行查询、分析结果，还能调度定时任务。

支持任意 **OpenAI 兼容**的模型提供商（OpenAI、本地 Ollama 等），开箱支持 **MySQL** 和 **PostgreSQL**。

## 快速开始

### Docker（推荐）

```bash
docker run -d -p 8000:8000 -v ~/.praxis/data:/app/data sunzy2/praxis:latest
```

打开 [http://localhost:8000](http://localhost:8000)，按引导向导连接你的第一个数据库。

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

### 配置

将 `.env.example` 复制为 `.env`，配置你的 AI 提供商：

```bash
# 使用 OpenAI
AI_BASE_URL=https://api.openai.com/v1
AI_API_KEY=sk-your-key
AI_MODEL=gpt-4

# 或使用本地模型（如 Ollama）
AI_BASE_URL=http://localhost:11434/v1
AI_MODEL=llama3
```

## 功能特性

### 和数据库对话

用自然语言和数据库交流。提问题，得到 SQL + 结果，在上下文中持续迭代。Agent 自动编写、解释和执行查询。

### 自定义智能体

创建专属 Agent，配置提示词、工具、技能和数据源绑定。每个 Agent 可以专注不同领域——DBA 诊断、业务报表、数据质检等。

### 可插拔技能

技能是 Markdown 格式的提示词模块，赋予 Agent 领域专业能力（如分层诊断策略、慢查询分析）。用 YAML Front Matter 编写，无需写代码。

### SQL 分析

粘贴一条 SQL，获取执行计划分析、改写建议和 AI 驱动的性能洞察。

### 知识库

上传文档构建知识库。Agent 对话时可引用知识库内容，提供更精准、有依据的回答。

### 函数与调度

定义基于 SQL 模板的可复用数据查询函数，可视化构建和测试。支持 cron 表达式或固定间隔调度，自动化报表生成和监控任务。

### 渠道

对接外部消息平台（Slack、钉钉等），让用户在 Praxis 界面之外也能与 Agent 交互。

## 架构

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

## 本地开发

### 前置要求

- Python 3.11+
- Node.js 18+
- [uv](https://github.com/astral-sh/uv)（Python 包管理器）

### 后端

```bash
make install       # 安装依赖 (uv sync)
make migrate       # 执行数据库迁移
make dev           # 启动 API 服务 :8000，热重载
```

### 前端

```bash
cd frontend
npm install
npm run dev        # Vite 开发服务 :5173
```

### Docker 构建

```bash
make docker-build  # 构建 praxis:latest
```

### 运行测试

```bash
make test          # 运行 pytest
make lint          # 运行 ruff + semantic guard
```

## 路线图

- [ ] 更多数据库支持（Oracle、SQL Server、ClickHouse……）
- [ ] 内置仪表盘与可视化
- [ ] 多用户与 RBAC
- [ ] 社区技能市场
- [ ] MCP（Model Context Protocol）集成

## 参与贡献

欢迎任何形式的贡献！无论是 Bug 报告、功能建议还是 Pull Request。

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

## 社区

- [GitHub Issues](https://github.com/nicholasgasior/praxis-ce/issues) — Bug 报告与功能建议
- [GitHub Discussions](https://github.com/nicholasgasior/praxis-ce/discussions) — 提问与交流

## 许可证

Praxis 社区版开源发布，详见 [LICENSE](LICENSE)。
