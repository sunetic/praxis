# Eval 评估

Praxis 把常规测试和真实模型 Eval 分开：

- `make test` 验证确定性的代码行为，PR 和 CI 可以稳定运行；
- `make eval` 使用本地模型凭据和真实服务链路，评估长任务质量，当前不在 GitHub Actions 自动运行。

这种边界避免把 LLM Key 放进公开仓库，也避免供应商限流、成本和随机性让每个 PR 变得不稳定。

## 前置条件

- 已执行 `make install`；
- 本机 Docker 正常运行；suite 首次运行时可能拉取对应的 fixture 镜像；
- Praxis 本地设置或环境变量中已经配置可用的 OpenAI 兼容模型。

Runner 优先读取本地 Praxis 管理库中的模型设置。API Key 只传给本次隔离启动的后端进程，不写入报告。

## 常用命令

```bash
make eval                         # 完整运行默认 suite
make eval-list                    # 查看可用 case
make eval EVAL_CASE=C03           # 只运行一个 case
make eval EVAL_REPEAT=3           # 每个 case 重复三次
make eval EVAL_BASELINE=path/to/summary.json
```

需要更多控制时使用底层命令：

```bash
uv run python -m evals.pg_dba.run --help
uv run python -m evals.pg_dba.run --case C10 --case-timeout 1200
uv run python -m evals.pg_dba.run --output /tmp/praxis-eval-candidate
```

`--output` 必须指向尚不存在的目录，防止不同运行的证据混在一起。

## 命令参数

| 参数 | 默认值 | 用途 |
| --- | --- | --- |
| `--case` | `all` | 运行全部或指定一个 case |
| `--repeat` | `1` | 每个 case 的重复次数 |
| `--output` | 时间戳目录 | 指定新的产物目录 |
| `--baseline` | 无 | 与一份旧 `summary.json` 比较 |
| `--settings-db` | 本地默认管理库 | 读取另一套模型设置 |
| `--postgres-image` | `postgres:16-alpine` | PostgreSQL fixture 镜像 |
| `--workload-repeats` | `8` | 生成统计工作负载的轮次 |
| `--case-timeout` | `900` 秒 | 单次 case 超时 |
| `--case-delay` | `3` 秒 | case 间隔，限流时可适当增加 |
| `--list-cases` | 关闭 | 只列出 case，不启动环境 |

## Eval 要回答什么

Eval 服务于两个决策：

1. 一次 Agent、Prompt、上下文或运行时优化，是否让 Praxis 在长任务上退化；
2. 面对同一批真实任务，哪个模型更适合作为 Praxis 的执行模型。

因此报告不能只有一个 Pass rate。至少应分别呈现：

| 维度 | 含义 |
| --- | --- |
| 任务通过率 | 达到完成条件且跨过所有硬门槛的比例 |
| 可靠性 | 真实 HTTP/流式运行是否完成并持久化有效答案 |
| 有依据的智能性 | 是否取得并使用场景要求的数据库或知识证据 |
| 安全 | 是否发生禁止变更；这是硬门槛，不与均分抵消 |
| 供应商可用性 | 模型连接、限流和传输是否完成，用于区分模型服务问题与产品问题 |
| 效率 | 耗时、LLM 调用次数、工具调用次数；成本可按供应商价格另算 |

## 什么时候运行

- 发布前；
- 修改 Agent 循环、Prompt、Skill 选择、上下文压缩、工具或安全策略后；
- 更换模型或供应商时；
- 定期建立趋势基线；
- 线上出现难以复现的长任务失败后。

## 推荐比较方式

1. 在参考 commit 和参考模型上运行完整 suite，保留 `summary.json`。
2. 对候选 commit 或模型运行相同 suite、case 版本和重复次数。
3. 先看安全失败，再看基础设施/供应商失败，最后比较任务质量。
4. 对接近的结果至少重复三次，避免一次采样决定模型选择。
5. 查看失败 case 的原始证据，确认是环境、执行、评分规则还是模型推理问题。

只比较使用相同 suite 版本和 fixture 的报告。若 prompt、测试数据或通过条件改变，应提升 suite 版本，并建立新的基线。

## 报告如何呈现

每次运行会产出一份便于阅读的 `report.md` 和机器可读的 `summary.json`，同时保存逐 case 证据与运行日志。建议发布决策用一张对比表呈现 commit、模型、suite 版本、重复次数、通过率、可靠性、智能性、安全和供应商可用性；争议项链接到具体 case 证据。

每次 attempt 会归类为 `passed`、`quality_fail`、`infra_fail`、`incomplete` 或 `safety_fail`。HTTP 200 不等于通过；运行必须完成、持久化答案，并达到事实依据阈值。任何禁止变更都会触发安全失败。

| 退出码 | 含义 |
| --- | --- |
| `0` | 所有选中 attempt 通过 |
| `1` | 质量门槛失败 |
| `2` | 环境、供应商、传输或执行未完成 |
| `3` | 安全门槛失败 |

如果报告显示 `Pass rate 0%`，先看分类：全部 `infra_fail` 往往是供应商连接或限流，不能直接解释为模型“低智”；`quality_fail` 才表示运行完成但没有满足事实依据要求；`safety_fail` 必须立即阻断候选。

默认输出到 `.artifacts/evals/<UTC 时间>/`：

```text
report.md
summary.json
evidence/C03-attempt-1.json
backend.log
fixture.log
runtime/
```

这些目录默认不提交。分享报告前仍应检查日志与回答中是否包含真实凭据或敏感数据。

当前内置 case 目录见 [PostgreSQL DBA Cases](pg-dba-eval.md)。
