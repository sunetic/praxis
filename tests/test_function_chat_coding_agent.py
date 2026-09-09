from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.chat import ChatService
from app.services.function.chat_agent import FunctionChatAgent
from app.services.function.coding_tools import FunctionCodingTools


def _tool_response(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


def _text_response(content: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "delta": {"content": content},
                "finish_reason": "stop",
            }
        ]
    }


class _SequenceLLM:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages, tools=None, stream=False, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "stream": stream,
                "kwargs": kwargs,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        yield self.responses.pop(0)


@pytest.mark.asyncio
async def test_function_chat_agent_analysis_uses_shared_chat_runtime_without_tools(
    tmp_path: Path,
) -> None:
    llm = _SequenceLLM(
        [
            _text_response(
                json.dumps(
                    {
                        "result_status": "too_complex",
                        "result": "Split the request into two independent Functions.",
                    }
                )
            )
        ]
    )
    agent = FunctionChatAgent(chat_service=ChatService(llm=llm))

    result = await agent.run_coding_task(
        goal="Assess this multi-purpose request",
        workspace_dir=tmp_path,
        purpose="analyze",
    )

    assert result.result_status == "too_complex"
    assert result.changed_files == []
    assert "Split the request" in result.assistant_message
    assert not llm.calls[0]["tools"]


@pytest.mark.asyncio
async def test_function_chat_agent_build_reuses_reasoning_loop_and_function_tools(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text(
        "def main(payload, context):\n    return {'old': True}\n",
        encoding="utf-8",
    )
    code = "def main(payload, context):\n    return {'ok': True, 'value': payload.get('value')}\n"
    llm = _SequenceLLM(
        [
            _tool_response("write", "function_source_replace", {"code": code}),
            _tool_response(
                "probe",
                "function_runtime_probe",
                {"payload": {"value": "sample"}},
            ),
            _tool_response(
                "finish",
                "function_build_finish",
                {
                    "outcome": "completed",
                    "assistant_message": "Function 已完成并通过运行验证。",
                    "diff_summary": "Updated main.py",
                    "tests_suggested": ["Invoke with a representative value"],
                    "risk_notes": [],
                },
            ),
            _text_response("Function 已完成并通过运行验证。"),
        ]
    )
    events: list[dict[str, Any]] = []
    agent = FunctionChatAgent(chat_service=ChatService(llm=llm))

    result = await agent.run_coding_task(
        goal="Return the provided value",
        workspace_dir=tmp_path,
        purpose="implement",
        max_iterations=8,
        event_callback=events.append,
    )

    assert result.result_status == "completed"
    assert result.changed_files == ["main.py"]
    assert result.diff_summary == "Updated main.py"
    assert (tmp_path / "main.py").read_text(encoding="utf-8") == code
    assert any(
        event.get("payload", {}).get("agent_event_type") == "tool_result" for event in events
    )
    assert all(call["stream"] is True for call in llm.calls)


@pytest.mark.asyncio
async def test_function_chat_agent_repairs_failed_runtime_probe_in_same_reasoning_loop(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text(
        "def main(payload, context):\n    return {'old': True}\n",
        encoding="utf-8",
    )
    invalid_code = "def main(payloadmore main(payload, context):\n    return {'ok': True}\n"
    valid_code = "def main(payload, context):\n    return {'ok': True}\n"
    llm = _SequenceLLM(
        [
            _tool_response("bad-write", "function_source_replace", {"code": invalid_code}),
            _tool_response("bad-probe", "function_runtime_probe", {"payload": {}}),
            _tool_response("fix-write", "function_source_replace", {"code": valid_code}),
            _tool_response("good-probe", "function_runtime_probe", {"payload": {}}),
            _tool_response(
                "finish",
                "function_build_finish",
                {
                    "outcome": "completed",
                    "assistant_message": "已修复候选代码并通过运行验证。",
                    "diff_summary": "Repaired main.py after probe diagnostics",
                },
            ),
            _text_response("已修复候选代码并通过运行验证。"),
        ]
    )
    events: list[dict[str, Any]] = []
    agent = FunctionChatAgent(chat_service=ChatService(llm=llm))

    result = await agent.run_coding_task(
        goal="Build a valid Function",
        workspace_dir=tmp_path,
        purpose="implement",
        max_iterations=10,
        event_callback=events.append,
    )

    assert result.result_status == "completed"
    assert (tmp_path / "main.py").read_text(encoding="utf-8") == valid_code
    tool_results = [
        event.get("payload", {}).get("data", {})
        for event in events
        if event.get("payload", {}).get("agent_event_type") == "tool_result"
    ]
    assert any(item.get("success") is False for item in tool_results)


@pytest.mark.asyncio
async def test_function_finish_rejects_unverified_source_revision(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "def main(payload, context):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    tools = FunctionCodingTools(workspace_dir=tmp_path)

    result = await tools.execute(
        "function_build_finish",
        {
            "outcome": "completed",
            "assistant_message": "Done",
        },
    )

    assert result["success"] is False
    assert result["error"]["code"] == "verification_required"
    assert tools.state.completion is None


@pytest.mark.asyncio
async def test_source_change_invalidates_previous_runtime_probe(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "def main(payload, context):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    tools = FunctionCodingTools(workspace_dir=tmp_path)
    probe = await tools.execute("function_runtime_probe", {"payload": {}})
    assert probe["success"] is True

    changed = await tools.execute(
        "function_source_replace",
        {"code": "def main(payload, context):\n    return {'changed': True}\n"},
    )
    assert changed["success"] is True

    finish = await tools.execute(
        "function_build_finish",
        {"outcome": "completed", "assistant_message": "Done"},
    )
    assert finish["success"] is False
    assert finish["error"]["code"] == "verification_required"


def test_function_coding_event_translation_excludes_internal_state_and_source() -> None:
    events: list[dict[str, Any]] = []

    FunctionChatAgent._emit_coding_event(
        events.append,
        {
            "type": "task_state",
            "data": {"journal": "internal prompt and source"},
            "meta": {"iteration": 4},
        },
    )
    FunctionChatAgent._emit_coding_event(
        events.append,
        {
            "type": "tool_start",
            "data": {
                "tool_call_id": "call-write",
                "name": "function_source_replace",
                "arguments": {"code": "SECRET_SOURCE"},
            },
            "meta": {"iteration": 2, "private": "SECRET_META"},
        },
    )
    FunctionChatAgent._emit_coding_event(
        events.append,
        {
            "type": "tool_result",
            "data": {
                "tool_call_id": "call-read",
                "name": "function_source_read",
                "result": {
                    "success": True,
                    "data": {
                        "content": "SECRET_SOURCE",
                        "revision": "sha256:test",
                        "total_lines": 42,
                    },
                },
            },
            "meta": {"iteration": 3},
        },
    )

    assert len(events) == 2
    serialized = json.dumps(events)
    assert "SECRET_SOURCE" not in serialized
    assert "SECRET_META" not in serialized
    assert events[1]["payload"]["data"]["result"] == {"revision": "sha256:test"}
