# Agent Runtime 测试

本目录验证原生 Pydantic AI 运行时的确定性契约：消息持久化、工具调用、审批、恢复、上下文管理、事件流以及产品入口接线。测试通过只代表这些契约成立，不代表真实模型的回答质量已经验收。

## 确定性测试

在仓库根目录运行：

```bash
make test-agent-runtime
```

也可以随完整后端测试一起运行：

```bash
uv run pytest -m "not llm"
```

默认测试禁止真实模型网络请求。脚本模型和内存 transport 只验证协议与状态边界，不作为智能程度或端到端体验证据。

## 真实模型冒烟

显式提供隔离测试端点的配置：

- `PRAXIS_RUNTIME_LIVE=1`
- `PRAXIS_RUNTIME_LIVE_MODEL`
- `PRAXIS_RUNTIME_LIVE_BASE_URL`
- `PRAXIS_RUNTIME_LIVE_API_KEY`

然后运行：

```bash
uv run pytest tests/agent_runtime/test_live.py -m llm -q
```

真实模型测试只使用合成内存数据，不连接业务数据源，也不会从仓库文件读取凭据。

## 浏览器与产品链路

前端单元测试和确定性浏览器回放分别运行：

```bash
cd frontend
npm test -- --maxWorkers=1
npm run test:e2e:ci
```

需要真实服务、模型或预置样本的 `frontend/e2e/live-*.spec.ts` 用例均为显式启用，不属于默认 CI。实际验收记录应由 CI artifact 或独立测试报告保存，不在本文件累计维护。
