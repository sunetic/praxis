# Praxis 文档

Praxis 是一个可自部署的 **AI 原生数据库 Agent 平台**。它把数据库连接、自然语言对话、专业知识、可复用 Agent、Function 与定时任务放进同一个工作空间，让数据库工作从一次性的问答逐步沉淀为可以重复运行的能力。

Praxis 目前面向 MySQL 和 PostgreSQL，并可连接 OpenAI 兼容的模型服务。

## 从哪里开始

| 如果你想…… | 建议阅读 |
| --- | --- |
| 先理解 Praxis 解决什么问题 | [项目定位与设计理念](concepts/philosophy.md) |
| 在本机快速跑起来 | [Quickstart](getting-started/quickstart.md) |
| 完成第一次数据库对话 | [数据源](features/datasources.md) → [Chat](features/chat.md) |
| 把一次有效流程保存下来 | [Agent](features/agents.md) 与 [Skills](features/skills.md) |
| 让任务自动执行 | [Function](features/functions.md) 与 [Scheduler](features/schedulers.md) |
| 了解长任务为什么更可靠 | [长任务可靠性](reliability/long-tasks.md) 与 [可观测性](reliability/observability.md) |
| 比较版本或模型的真实表现 | [Eval 评估](reliability/evaluation.md) |

## 文档范围

这里是面向使用者和贡献者的产品文档，介绍背景、设计思路、操作方式、可靠性机制与公开边界。API 的精确请求结构以运行中服务的 `/docs`（OpenAPI）为准。

仓库内部的需求草案、工程编排流程和实现笔记不属于公开文档，也不会进入本站导航或发布产物。
