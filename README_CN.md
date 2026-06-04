# Praxis

[English](README.md) | [中文](README_CN.md)

**AI 原生数据库 Agent 平台。** 把你的数据库变成自治的 AI 工作空间。

## 快速开始

```bash
docker run -p 8000:8000 sunzy2/praxis:latest
```

打开 [http://localhost:8000](http://localhost:8000)，按引导向导连接你的第一个数据库。

## Praxis 是什么

Praxis 是一个可自部署的平台，把你的数据库变成 AI 对话工作空间。不用手写 SQL，用自然语言描述你的需求——Praxis Agent 理解你的表结构，自动编写和执行查询、分析结果，还能调度定时任务。

开箱支持 **MySQL** 和 **PostgreSQL**。

## 功能一览

**对话** — 和数据库聊天。提问题，得到 SQL + 结果，在上下文中持续迭代。Agent 自动编写、解释和执行查询。

**智能体** — 创建自定义 Agent，配置专属提示词、工具、技能和数据源绑定。每个 Agent 可以专注不同领域（DBA 诊断、业务报表、数据质检等）。

**技能** — 可插拔的提示词模块，赋予 Agent 领域专业能力（如分层诊断策略、慢查询分析）。用 Markdown + YAML Front Matter 编写即可。

**SQL 分析** — 粘贴一条 SQL，获取执行计划分析、改写建议和 AI 驱动的性能洞察。

**知识库** — 上传文档构建知识库。Agent 对话时可引用知识库内容，提供更精准的回答。

**数据源** — 注册 MySQL / PostgreSQL 连接。Agent 通过内置的 `execute_sql` 和 `explain_sql` 工具直接查询。

**函数** — 定义基于 SQL 模板的可复用数据查询函数。可视化构建和测试，然后提供给 Agent 使用或调度自动执行。

**调度** — 按 cron 表达式或固定间隔运行函数。自动化周期性数据采集、报表生成和监控任务。

**渠道** — 对接外部消息平台，让用户在 Praxis 界面之外也能与 Agent 交互。

## 本地开发

```bash
# 后端
make install       # 安装依赖 (uv sync)
make migrate       # 执行数据库迁移
make dev           # 启动 API 服务 :8000，热重载

# 前端（另开终端）
cd frontend
npm install
npm run dev        # Vite 开发服务 :5173
```

### Docker 构建

```bash
docker build -t praxis:latest .
```

## 许可证

详见 [LICENSE](LICENSE)。
