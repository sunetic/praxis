# 可观测性

Praxis 的可观测性用于回答三个问题：用户看见了什么、Agent 实际做了什么、时间消耗或失败发生在哪里。

## 用户可见的运行证据

Chat、Function 和 Scheduler 会展示步骤、工具结果、状态、耗时或运行历史。高风险动作还会形成待确认状态。这些信息用于现场判断和失败恢复，不应混进普通用户消息冒充对话内容。

## 全链路追踪

Praxis 使用 OpenTelemetry 采集 HTTP、模型请求、部分数据库访问、Chat 工具调用和 Scheduler 运行等 span，并默认写入本地 SQLite 追踪库。追踪可通过 API 查询：

| 请求 | 用途 |
| --- | --- |
| `GET /api/v1/traces?minutes=60&limit=50` | 最近 trace |
| `GET /api/v1/traces/slow?threshold_ms=1000` | 慢 trace |
| `GET /api/v1/traces/{trace_id}` | 一条 trace 的 span 树 |
| `POST /api/v1/traces/cleanup` | 清理过期 span |

相关环境配置包括 `TRACING_ENABLED`、`TRACING_DB_PATH`、`TRACING_SAMPLE_RATE` 和 `TRACING_RETENTION_HOURS`。在高流量环境降低采样率前，应确认关键故障仍有足够样本。

## 排障顺序

1. 先看用户界面的最终状态和错误分类。
2. 如果是模型连接或限流，检查供应商响应与重试情况。
3. 如果任务中途停止，查看 Chat/Function/Scheduler 的运行事件和工具结果。
4. 如果是慢请求或跨组件问题，用 trace 定位耗时 span。
5. 涉及平台对象变更时，再核对确认状态和审计记录。

追踪数据可能包含 SQL 摘要、请求属性和错误信息。生产环境应限制追踪 API 与文件访问权限，设置合理保留时间，并避免把原始追踪库直接公开。

## 可观测性与 Eval 的分工

可观测性解释单次运行发生了什么；[Eval](evaluation.md) 则用固定任务反复测量版本和模型是否退化。二者需要同时存在：没有运行证据，Eval 失败难以定位；没有稳定 Eval，单次 trace 也无法说明整体质量趋势。
