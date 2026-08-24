# Eval 评估

Praxis 把常规测试和真实模型 Eval 分开：

- `make test` 验证任务状态转换、重试上限、checkpoint、恢复、解析与安全 guard 等确定性机制，PR 和 CI 可以稳定运行；
- `make eval` 使用本地模型凭据和隔离数据库 fixture，评估非确定性的任务结果，当前不在 GitHub Actions 自动运行。

这种边界避免把 LLM Key 放进公开仓库，也避免供应商限流、成本和随机性让每个 PR 变得不稳定。

## 前置条件

- 已执行 `make install`；
- 本机 Docker 正常运行；suite 首次运行时可能拉取对应的 fixture 镜像；
- Praxis 本地设置或环境变量中已经配置可用的 OpenAI 兼容模型。

Runner 优先读取本地 Praxis 管理库中的模型设置。API Key 只存在于本地进程内存，不写入 case 或报告。

## 常用命令

```bash
make eval                                      # 运行全部 PostgreSQL case
make eval-list                                 # 查看 PostgreSQL case
make eval EVAL_SUITE=mysql                     # 运行全部 MySQL case
make eval-list EVAL_SUITE=mysql                # 查看 MySQL case
make eval EVAL_SUITE=mysql EVAL_CASE=M03       # 只运行一个 MySQL case
make eval EVAL_SUITE=mysql EVAL_REPEAT=3       # 每个 MySQL case 重复三次
make eval EVAL_SUITE=mysql EVAL_PROFILE=model  # 用固定 harness 对比模型本身
make eval EVAL_EXPECTED_MODEL=DeepSeek-V4-Flash-0731
make eval EVAL_BASELINE=path/to/summary.json
```

需要更多控制时使用底层命令：

```bash
uv run python -m evals.pg_dba.run --help
uv run python -m evals.pg_dba.run --case C10 --case-timeout 1200
uv run python -m evals.mysql_dba.run --help
uv run python -m evals.mysql_dba.run --case M10 --case-delay 10
uv run python -m evals.mysql_dba.run --profile model --case M03 --repeat 3
uv run python -m evals.mysql_dba.run --output /tmp/praxis-mysql-eval-candidate
```

`--output` 必须指向尚不存在的目录，防止不同运行的证据混在一起。

完整真实 suite 会串行运行 case，让它们共享同一套稳定 fixture，同时避免数据库会话互相干扰。因此总耗时基本等于各次 Chat 执行时间之和：十个平均五分钟的 case 大约需要五十分钟，fixture 启动、评分和报告生成通常只占很小一部分。模型的工具与验证循环仍可能让完整运行从几十分钟延长到数小时，并消耗大量 token。开发时先用 `--case`；发布或模型选型时再跑完整 suite 并保留报告。比较模型时，不要只为某一个候选缩短 timeout。

## 命令参数

| 参数 | 默认值 | 用途 |
| --- | --- | --- |
| `--case` | `all` | 运行全部或指定一个 case |
| `--repeat` | `1` | 每个 case 的重复次数 |
| `--output` | 时间戳目录 | 指定新的产物目录 |
| `--baseline` | 无 | 与一份旧 `summary.json` 比较 |
| `--settings-db` | 本地默认管理库 | 读取另一套模型设置 |
| `--expected-model` | 无 | 解析出的模型不完全一致时，在访问供应商前终止 |
| `--postgres-image` | `postgres:16-alpine` | PostgreSQL suite fixture 镜像 |
| `--mysql-image` | `mysql:8.4` | MySQL suite fixture 镜像 |
| `--workload-repeats` | `8` | 生成统计工作负载的轮次 |
| `--case-timeout` | `300` 秒 | 单个 case 的总执行截止时间，而非单次请求或读取空闲超时 |
| `--case-delay` | `3` 秒 | case 间隔，限流时可适当增加 |
| `--profile` | `praxis` | `praxis` 评估完整产品链路；`model` 使用固定模型对比 harness |
| `--max-tool-rounds` | `20` | 固定模型 harness 的工具轮次上限 |
| `--list-cases` | 关闭 | 只列出 case，不启动环境 |

## 目的、机制与边界

Eval 服务于两个决策：

1. 一次 Agent、Prompt、上下文或运行时优化，是否让 Praxis 在长任务和复杂任务上退化；
2. 面对同一批真实任务，哪个模型更适合作为 Praxis 的执行模型。

一个 Eval case 由任务、隔离环境、参考事实或目标状态、可选权威来源要求和评分器组成。Runner 把任务交给所选 profile，观察最终答案与外部可见状态，执行正确性和安全检查，并把执行轨迹单独记录下来。重复运行用于衡量稳定性，避免用一次随机采样代表模型能力。

Eval 评估的是**最终取得了什么结果**，而不是**系统选择了怎样的过程**。它不规定内部规划、工具选择、工具顺序、是否使用知识库或最低调用次数。`minimum_tool_calls`、`minimum_sql_calls`、“必须调用知识库”等规则都不应成为 Eval 的通过条件：强模型可能已经掌握公开知识，也可能通过另一条有效路径得到同样正确的结果。

纯输入输出比较有两个必要例外：

- 安全检查可以观察真实环境中的禁止变更或副作用；一份看似正确的答案不能掩盖不安全执行。
- 当答案依赖私有、本地或明确指定的权威信息时，case 可以要求证据；评分器检查相关结论是否得到支撑，而不要求必须调用某个检索工具。

任务状态转换、checkpoint、恢复、重试上限、解析、工具协议和 verifier 控制流等确定性实现机制属于 tests。供应商连接和限流属于基础设施可用性；Eval 会单独报告，以免把供应商故障误判为模型低智，但它们不是任务质量标准。

候选比较采用有先后顺序的规则，而不是把过程指标混成一个总分：

1. 安全是硬门槛；
2. 正确性、完整性、必需证据和答案质量决定任务是否通过；
3. 重复运行结果决定稳定性；
4. 只有当结果质量和稳定性相当时，更少的工具调用、失败调用或重试，更低的耗时和 token 消耗，才代表更高的执行效率。

过程指标可以解释结果或用于同质量候选之间的选择，但不能挽救错误答案，也不能因为正确答案采用了意外路径而判其失败。

因此报告不能只有一个 Pass rate。至少应分别呈现：

| 维度 | 含义 |
| --- | --- |
| 任务通过率 | 达到完成条件且跨过所有硬门槛的比例 |
| 稳定通过率 | 每次重复运行都通过的 case 比例 |
| 可靠性 | 所选运行链路是否完成并产生有效答案 |
| 任务结果 | 参考事实与目标状态的覆盖度，是主要正确性指标 |
| 答案质量 | 风险排序、不确定性、取舍和其他场景化表达标准 |
| 必需证据 | 仅当 case 明确指定权威来源时，检查来源特有结论是否获得支撑 |
| 安全 | 是否发生禁止变更；这是硬门槛，不与均分抵消 |
| 供应商可用性 | 模型连接、限流和传输是否完成，用于区分模型服务问题与产品问题 |
| 轨迹诊断 | 耗时、token、工具调用、失败调用、重试与 verifier 次数；用于解释表现，不是规定动作 |

系统回归使用 `praxis` profile：通过真实后端和 Chat 链路评估“模型 + Praxis Agent harness”。模型选型使用 `model` profile：固定一个精简的 OpenAI 兼容工具循环，只替换候选模型。两者共享 case、fixture、参考答案、安全门禁和报告结构，但 baseline 只能在同一 profile 内比较。

## 什么时候运行

- 发布前；
- 修改 Agent 循环、Prompt、Skill 选择、上下文压缩、工具或安全策略后；
- 更换模型或供应商时；
- 定期建立趋势基线；
- 线上出现难以复现的长任务失败后。

## 推荐比较方式

1. 先确定决策类型：产品回归用 `praxis`，模型选型用 `model`。
2. 在参考 commit 和参考模型上运行完整 suite，保留 `summary.json`。
3. 候选运行必须使用相同 profile、suite、case 版本、timeout 和重复次数。
4. 先看安全失败，再看基础设施/供应商失败，最后比较任务结果。
5. 对接近的结果至少重复三次，避免一次采样决定模型选择。
6. 查看原始证据和轨迹诊断，确认问题来自环境、执行、评分、harness 还是模型推理。

只比较使用相同 suite 版本和 fixture 的报告。若 prompt、测试数据或通过条件改变，应提升 suite 版本，并建立新的基线。

正式运行建议设置 `EVAL_EXPECTED_MODEL`。这样默认本地设置库不可用或为空时，不会悄悄改用 `.env` 中的其他模型。显式传入的 `--settings-db` 若不存在或不可读，会直接失败而不是回退。

## 报告如何呈现

每次运行会产出便于阅读的 `report.md`、机器可读的 `summary.json`、逐 case 证据与运行日志。发布决策应比较 profile、commit、模型、suite 版本、重复次数、通过率、稳定通过率、任务结果、答案质量、必需证据、安全和供应商可用性；争议项链接到具体 case 证据。

每次 attempt 会归类为 `passed`、`quality_fail`、`infra_fail`、`incomplete` 或 `safety_fail`。运行必须完成、产生答案、达到结果与质量阈值、满足该 case 的证据要求，并跨过安全门禁。任何禁止变更都会触发安全失败。

| 退出码 | 含义 |
| --- | --- |
| `0` | 所有选中 attempt 通过 |
| `1` | 质量门槛失败 |
| `2` | 环境、供应商、传输或执行未完成 |
| `3` | 安全门槛失败 |

如果报告显示 `Pass rate 0%`，先看分类：全部 `infra_fail` 往往是供应商连接或限流，不能直接解释为模型“低智”；`quality_fail` 表示运行完成但没有达到任务结果、答案质量或该 case 的证据阈值；`safety_fail` 必须立即阻断候选。

PostgreSQL 默认输出到 `.artifacts/evals/<UTC 时间>/`，MySQL 默认输出到 `.artifacts/evals/mysql/<UTC 时间>/`：

```text
report.md
summary.json
evidence/C03-attempt-1.json
backend.log                 # 仅 praxis profile
fixture.log
runtime/
```

这些目录默认不提交。分享报告前仍应检查日志与回答中是否包含真实凭据或敏感数据。

当前内置 case 目录见 [PostgreSQL DBA Cases](pg-dba-eval.md) 和 [MySQL DBA Cases](mysql-dba-eval.md)。
