import inspect

import pytest

from app.models import models
from app.services.agent.core import summarize_build_goal
from app.services.function.chat_agent import FunctionChatAgent
from app.services.function.scope_adapter import FunctionBuildScopeAdapter


class _ContinuationTrueLLM:
    async def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        stream: bool = False,
        **kwargs: object,
    ):
        del messages, tools, stream, kwargs
        yield {"choices": [{"message": {"content": '{"continue_previous": true}'}}]}


@pytest.mark.parametrize(
    "method_name",
    ["build_function_draft", "suggest_function_input", "invoke_function"],
)
def test_function_agent_actions_do_not_accept_unused_chat_context(method_name: str) -> None:
    method = getattr(FunctionChatAgent, method_name)
    assert "context" not in inspect.signature(method).parameters


def test_chat_agent_compose_function_goal_uses_retry_history():
    agent = FunctionChatAgent(
        function_scope_adapter=FunctionBuildScopeAdapter(llm_client=_ContinuationTrueLLM())
    )
    goal = agent.compose_function_build_goal(
        prompt="再次构建",
        recent_contexts=[
            {
                "prompt": "入参是个数，根据个数返回 datasource 列表",
                "status": "failed",
                "error": "runtime invocation mismatch between expected and actual function args",
                "summary": "Function 构建失败",
            },
            {
                "prompt": "根据 datasource_id 查询库名",
                "status": "failed",
                "error": "private module import failed at runtime",
                "summary": "Function 构建失败",
            },
        ],
        conversation_context="user: 再次构建\nassistant: 上次构建失败，正在修复",
    )
    assert "Primary Requirement:\n入参是个数，根据个数返回 datasource 列表" in goal
    assert "no mock/fake return data" in goal
    assert "get_function_runtime_contract" in goal


def test_chat_agent_compose_function_goal_mentions_scheduler_history_for_governance_tasks():
    agent = FunctionChatAgent()
    goal = agent.compose_function_build_goal(
        prompt="构建一个 Function，清理 30 天前的 Scheduler 运行历史，Build 里先 dry-run 验证",
        recent_contexts=[],
        conversation_context="",
    )
    assert "scheduler_history" in goal
    assert "datasource SQL" in goal


def test_function_scope_adapter_rejects_non_function_target():
    adapter = FunctionBuildScopeAdapter()
    try:
        adapter.apply_goal(workspace_store=None, target=models.Page(), goal="x")  # type: ignore[arg-type]
    except TypeError as exc:
        assert "models.Function" in str(exc)
    else:
        raise AssertionError("expected TypeError")


def test_function_scope_adapter_guardrails_use_global_capabilities():
    guardrails = FunctionBuildScopeAdapter().guardrails()
    assert "injected globals" in guardrails
    assert "context.platform" in guardrails
    assert "context.get('datasource_id')" in guardrails


def test_summarize_build_goal_uses_primary_requirement_body():
    goal = (
        "Primary Requirement:\n"
        "输入租户 id，返回该租户统计信息收集任务的健康情况\n\n"
        "Current Build Conversation (latest turns):\n"
        "user: 再次构建"
    )

    assert summarize_build_goal(goal) == "输入租户 id，返回该租户统计信息收集任务的健康情况"
