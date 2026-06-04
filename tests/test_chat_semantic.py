"""
End-to-end semantic tests for ChatService tool selection.

These tests verify that the ChatService ReAct loop makes correct tool
decisions given specific user inputs and system contexts.  They use a
ScriptedLLM mock that returns predefined responses so we can assert the
full pipeline behavior without a live LLM.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest

from app.services.chat import ChatService


@pytest.fixture
def anyio_backend():
    return "asyncio"


class ScriptedLLM:
    """LLM mock returning a predefined sequence of responses.

    Each entry in *responses* is yielded as OpenAI-compatible streaming
    chunks when ``chat()`` is called.  Entries are consumed in order;
    calling beyond the list raises ``IndexError`` (test author error).
    """

    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.call_index = 0
        self.captured_calls: list[dict] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs: object,
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.captured_calls.append({"messages": messages, "tools": tools})
        resp = self.responses[self.call_index]
        self.call_index += 1

        if resp.get("tool_calls"):
            for tc in resp["tool_calls"]:
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                                        "function": {
                                            "name": tc["function"]["name"],
                                            "arguments": json.dumps(
                                                tc["function"]["arguments"],
                                                ensure_ascii=False,
                                            ),
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
        elif resp.get("content"):
            yield {
                "choices": [
                    {"delta": {"content": resp["content"]}, "finish_reason": None}
                ]
            }
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        else:
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}


def _text_response(content: str) -> dict:
    return {"content": content}


def _tool_call_response(name: str, arguments: dict, call_id: str | None = None) -> dict:
    return {
        "tool_calls": [
            {
                "id": call_id or f"call_{uuid.uuid4().hex[:8]}",
                "function": {"name": name, "arguments": arguments},
            }
        ]
    }


def _tool_result(success: bool = True, data: dict | None = None, error: str | None = None):
    from app.tools.registry import ToolResult
    if success:
        return ToolResult(success=True, data=data or {})
    return ToolResult(success=False, error={"code": "test_error", "message": error or "test error"})


async def _collect_events(service: ChatService, messages: list[dict], **kwargs: Any) -> list[dict]:
    events: list[dict] = []
    async for event in service.chat_with_tools(messages, **kwargs):
        events.append(event)
    return events


def _find_events(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e.get("type") == event_type]


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_plain_query_no_tool_call():
    """A knowledge query should produce text only, no tool invocations."""
    scripted = ScriptedLLM([_text_response("OCP 的监控 API 主要包括...")])
    service = ChatService(llm=scripted)
    messages = [{"role": "user", "content": "OCP 的监控 API 接口是怎样的？"}]

    events = await _collect_events(service, messages, tools=[])

    tool_events = _find_events(events, "tool_start")
    assert len(tool_events) == 0, "Knowledge query should not trigger any tool call"
    text_events = _find_events(events, "assistant")
    assert any("OCP" in e.get("data", {}).get("text", "") for e in text_events)


@pytest.mark.anyio
async def test_tool_call_flows_through_react_loop():
    """When LLM decides to call a tool, ReAct loop should execute it and
    feed the result back, then LLM produces a final text response."""
    scripted = ScriptedLLM(
        [
            _tool_call_response("datasource_switch", {"datasource_id": 2}),
            _text_response("已切换到 sys 数据源"),
        ]
    )
    service = ChatService(llm=scripted)
    messages = [{"role": "user", "content": "切换到 sys 数据源"}]

    mock_tool = AsyncMock()
    mock_tool.execute = AsyncMock(return_value=_tool_result(
        success=True,
        data={"message": "已切到数据源 sys", "datasource_id": 2},
    ))

    with patch("app.services.chat.registry.get", return_value=mock_tool):
        events = await _collect_events(
            service, messages,
            tools=[{"type": "function", "function": {"name": "datasource_switch", "parameters": {}}}],
            conversation_id=1,
        )

    tool_events = _find_events(events, "tool_result")
    assert len(tool_events) == 1
    assert tool_events[0]["data"]["name"] == "datasource_switch"
    assert scripted.call_index == 2, "LLM should be called twice: plan + reflect/respond"


@pytest.mark.anyio
async def test_context_injection_conversation_id():
    """datasource_switch should receive auto-injected conversation_id."""
    scripted = ScriptedLLM(
        [
            _tool_call_response("datasource_switch", {"datasource_id": 3}),
            _text_response("done"),
        ]
    )
    service = ChatService(llm=scripted)

    mock_tool = AsyncMock()
    mock_tool.execute = AsyncMock(return_value=_tool_result(success=True, data={}))

    with patch("app.services.chat.registry.get", return_value=mock_tool):
        await _collect_events(
            service,
            [{"role": "user", "content": "切换到数据源 3"}],
            tools=[{"type": "function", "function": {"name": "datasource_switch", "parameters": {}}}],
            conversation_id=42,
        )

    mock_tool.execute.assert_called_once()
    call_kwargs = mock_tool.execute.call_args
    assert call_kwargs.kwargs.get("conversation_id") == 42 or \
        (call_kwargs.args and any(a == 42 for a in call_kwargs.args)), \
        "conversation_id should be injected into datasource_switch"


@pytest.mark.anyio
async def test_unknown_tool_returns_error():
    """When LLM calls a non-existent tool, system should return an error
    gracefully without crashing."""
    scripted = ScriptedLLM(
        [
            _tool_call_response("nonexistent_tool", {"arg": 1}),
            _text_response("抱歉，我无法执行该操作"),
        ]
    )
    service = ChatService(llm=scripted)

    events = await _collect_events(
        service,
        [{"role": "user", "content": "帮我分析性能"}],
        tools=[{"type": "function", "function": {"name": "nonexistent_tool", "parameters": {}}}],
    )

    tool_events = _find_events(events, "tool_result")
    assert len(tool_events) == 1
    result = tool_events[0].get("data", {}).get("result", {})
    assert result.get("success") is False
    assert "not found" in str(result.get("error", "")).lower()


@pytest.mark.anyio
async def test_scripted_llm_captures_system_prompt():
    """Verify that system prompt and tool definitions reach the LLM."""
    scripted = ScriptedLLM([_text_response("ok")])
    service = ChatService(llm=scripted)

    await _collect_events(
        service,
        [{"role": "user", "content": "hello"}],
        system_prompt="你是一个 DBA 助手",
        tools=[{"type": "function", "function": {"name": "execute_sql", "parameters": {}}}],
    )

    assert len(scripted.captured_calls) == 1
    call = scripted.captured_calls[0]
    assert any(m["role"] == "system" and "DBA" in m["content"] for m in call["messages"])
    assert call["tools"] is not None and len(call["tools"]) > 0
