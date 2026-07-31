from copy import deepcopy
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.chat import ChatService
from app.services.llm import RateLimitError
from app.tools.registry import BaseTool, ToolResult, registry


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeLLM:
    def __init__(self, responses: list[list[dict]]):
        self.responses = responses
        self.call_count = 0
        self.calls: list[list[dict]] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = True,
    ):
        del tools, stream
        self.calls.append(deepcopy(messages))
        idx = self.call_count
        self.call_count += 1
        if idx >= len(self.responses):
            return
            yield  # pragma: no cover
        for chunk in self.responses[idx]:
            yield chunk


class FakeRateLimitedLLM:
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = True,
    ):
        del messages, tools, stream
        raise RateLimitError(retries=5, wait_seconds=10)
        yield  # pragma: no cover


async def _collect_events(service: ChatService) -> list[dict]:
    events: list[dict] = []
    async for event in service.chat_with_tools(
        messages=[{"role": "user", "content": "test"}],
        tools=[],
        use_state_machine=True,
    ):
        events.append(event)
    return events


@pytest.mark.anyio
async def test_chat_service_success_path_emits_assistant_and_done():
    service = ChatService(max_iterations=2, max_reflections=1)
    service.llm = FakeLLM(
        responses=[
            [
                {
                    "choices": [
                        {
                            "delta": {"content": "hello"},
                        }
                    ]
                }
            ]
        ]
    )

    events = await _collect_events(service)
    event_types = [event["type"] for event in events]
    assert "assistant" in event_types
    assert event_types[-1] == "done"
    assert events[-1]["phase"] == "done"


@pytest.mark.anyio
async def test_chat_service_tool_failure_hits_retry_budget():
    tool_call_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tc-1",
                            "function": {"name": "unknown_tool", "arguments": "{}"},
                        }
                    ]
                }
            }
        ]
    }

    service = ChatService(max_iterations=3, max_reflections=1)
    service.llm = FakeLLM(responses=[[tool_call_chunk], [tool_call_chunk]])

    events = await _collect_events(service)
    error_events = [event for event in events if event["type"] == "error"]
    assistant_events = [event for event in events if event["type"] == "assistant"]
    assert not error_events
    assert assistant_events
    assert "阶段性结论" in (assistant_events[-1]["data"].get("text") or "")
    assert events[-1]["type"] == "done"
    assert events[-1]["data"]["text_emitted"] is True


@pytest.mark.anyio
async def test_chat_service_stream_interruption_still_emits_done():
    service = ChatService(max_iterations=1, max_reflections=1)
    service.llm = FakeLLM(responses=[[]])

    events = await _collect_events(service)
    assert events[-1]["type"] == "done"
    assert events[-1]["data"]["text_emitted"] is False


@pytest.mark.anyio
async def test_chat_service_malformed_provider_chunk_is_tolerated():
    service = ChatService(max_iterations=1, max_reflections=1)
    service.llm = FakeLLM(
        responses=[
            [
                {"foo": "bar"},
                {"choices": [{"delta": {"not_content": "x"}}]},
            ]
        ]
    )

    events = await _collect_events(service)
    assert events[-1]["type"] == "done"
    assert not [event for event in events if event["type"] == "error"]


@pytest.mark.anyio
async def test_chat_service_iteration_limit_returns_explicit_error():
    tool_call_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tc-limit",
                            "function": {"name": "unknown_tool", "arguments": "{}"},
                        }
                    ]
                }
            }
        ]
    }

    service = ChatService(max_iterations=1, max_reflections=5)
    service.llm = FakeLLM(responses=[[tool_call_chunk]])

    events = await _collect_events(service)
    error_events = [event for event in events if event["type"] == "error"]
    assistant_events = [event for event in events if event["type"] == "assistant"]
    assert not error_events
    assert assistant_events
    assert "阶段性结论" in (assistant_events[-1]["data"].get("text") or "")


@pytest.mark.anyio
async def test_chat_service_does_not_emit_planner_text_during_tool_iteration():
    chunk_with_content_and_tool = {
        "choices": [
            {
                "delta": {
                    "content": "I will call tool now",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tc-emit",
                            "function": {"name": "unknown_tool", "arguments": "{}"},
                        }
                    ],
                }
            }
        ]
    }

    service = ChatService(max_iterations=1, max_reflections=0)
    service.llm = FakeLLM(responses=[[chunk_with_content_and_tool]])
    events = await _collect_events(service)

    assistant_text = "".join(
        (event.get("data") or {}).get("text", "")
        for event in events
        if event.get("type") == "assistant"
    )
    assert "I will call tool now" not in assistant_text
    assert "阶段性结论" in assistant_text


class DateTimeTool(BaseTool):
    name = "datetime_tool"
    description = "Return datetime payload"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **params: Any) -> ToolResult:
        del params
        return ToolResult(success=True, data={"now": datetime(2026, 1, 1, 0, 0, 0)})


@pytest.mark.anyio
async def test_chat_service_handles_datetime_in_tool_result_without_serialization_error():
    tool_call_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tc-datetime",
                            "function": {"name": "datetime_tool", "arguments": "{}"},
                        }
                    ]
                }
            }
        ]
    }
    final_text_chunk = {"choices": [{"delta": {"content": "done"}}]}

    tool = DateTimeTool()
    original = registry.tools.get(tool.name)
    registry.register(tool)
    try:
        service = ChatService(max_iterations=3, max_reflections=1)
        service.llm = FakeLLM(responses=[[tool_call_chunk], [final_text_chunk]])

        events = await _collect_events(service)
        error_events = [event for event in events if event["type"] == "error"]
        assert not any(
            "not JSON serializable" in (event["data"].get("message") or "")
            for event in error_events
        )
    finally:
        if original is not None:
            registry.tools[tool.name] = original
        else:
            registry.tools.pop(tool.name, None)


class ObjectMetaTool(BaseTool):
    name = "object_meta_tool"
    description = "Return object execution identifiers"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **params: Any) -> ToolResult:
        del params
        return ToolResult(
            success=True,
            data={
                "id": 42,
                "run_id": "run-42",
                "release": {"id": 9},
            },
        )


class PendingConfirmTool(BaseTool):
    name = "pending_confirm_tool"
    description = "Return requires_confirmation payload"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **params: Any) -> ToolResult:
        del params
        return ToolResult(
            success=True,
            data={
                "requires_confirmation": True,
                "action_token": "tok-1",
                "sql_preview": "CALL dbms_scheduler.enable('MONDAY_WINDOW');",
            },
        )


class ShouldNotRunTool(BaseTool):
    name = "should_not_run_tool"
    description = "Must not be executed when confirmation is pending"
    parameters = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.invoked = 0

    async def execute(self, **params: Any) -> ToolResult:
        del params
        self.invoked += 1
        return ToolResult(success=True, data={"invoked": self.invoked})


@pytest.mark.anyio
async def test_chat_service_stops_after_generic_batch_boundary():
    tool_call_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tc-boundary-1",
                            "function": {
                                "name": "should_not_run_tool",
                                "arguments": '{"_runtime":{"batch_boundary_after":true}}',
                            },
                        },
                        {
                            "index": 1,
                            "id": "tc-boundary-2",
                            "function": {"name": "pending_confirm_tool", "arguments": "{}"},
                        },
                    ]
                }
            }
        ]
    }

    boundary_tool = ShouldNotRunTool()
    blocked_tool = PendingConfirmTool()
    original_boundary = registry.tools.get(boundary_tool.name)
    original_blocked = registry.tools.get(blocked_tool.name)
    registry.register(boundary_tool)
    registry.register(blocked_tool)
    try:
        service = ChatService(max_iterations=3, max_reflections=1)
        fake_llm = FakeLLM(responses=[[tool_call_chunk]])
        service.llm = fake_llm

        events = await _collect_events(service)
        tool_starts = [event for event in events if event.get("type") == "tool_start"]
        tool_results = [event for event in events if event.get("type") == "tool_result"]
        reflects = [event for event in events if event.get("type") == "reflect"]

        assert len(tool_starts) == 1
        assert len(tool_results) == 1
        assert tool_results[0]["data"]["name"] == "should_not_run_tool"
        assert reflects
        assert boundary_tool.invoked == 1
        assert len(fake_llm.calls) == 2
        second_round = fake_llm.calls[1]
        assert any(message.get("role") == "tool" for message in second_round)
        assert any(message.get("tool_call_id") == "tc-boundary-1" for message in second_round)
    finally:
        if original_boundary is not None:
            registry.tools[boundary_tool.name] = original_boundary
        else:
            registry.tools.pop(boundary_tool.name, None)
        if original_blocked is not None:
            registry.tools[blocked_tool.name] = original_blocked
        else:
            registry.tools.pop(blocked_tool.name, None)


@pytest.mark.anyio
async def test_chat_service_uses_protocol_tool_messages_in_followup_round():
    tool_call_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tc-1",
                            "function": {"name": "unknown_tool", "arguments": "{}"},
                        }
                    ]
                }
            }
        ]
    }
    final_text_chunk = {"choices": [{"delta": {"content": "done"}}]}

    service = ChatService(max_iterations=2, max_reflections=1)
    fake_llm = FakeLLM(responses=[[tool_call_chunk], [final_text_chunk]])
    service.llm = fake_llm
    events = await _collect_events(service)
    error_events = [event for event in events if event["type"] == "error"]
    assistant_events = [event for event in events if event["type"] == "assistant"]
    assert not error_events
    assert assistant_events
    assert len(fake_llm.calls) == 2

    second_round = fake_llm.calls[1]
    assert any(message.get("role") == "tool" for message in second_round)
    assert any(message.get("tool_call_id") == "tc-1" for message in second_round)
    assert not any(
        message.get("role") == "user"
        and isinstance(message.get("content"), str)
        and "Tool `" in message.get("content")
        for message in second_round
    )


@pytest.mark.anyio
async def test_chat_service_marks_action_capability_as_not_tool():
    service = ChatService(max_iterations=1, max_reflections=0)
    result = await service._execute_tool_lifecycle(
        tool_call={
            "id": "tc-capability",
            "function": {
                "name": "datasource.switch_context",
                "arguments": '{"datasource_id": 2}',
            },
        },
        default_datasource_id=None,
        scope_context=None,
    )
    assert result["error_class"] == "dependency_error"
    error_payload = result["result"]["error"]
    assert isinstance(error_payload, dict)
    assert error_payload.get("code") == "capability_not_tool"


@pytest.mark.anyio
async def test_chat_service_stops_after_pending_confirmation():
    tool_call_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tc-confirm-1",
                            "function": {"name": "pending_confirm_tool", "arguments": "{}"},
                        },
                        {
                            "index": 1,
                            "id": "tc-confirm-2",
                            "function": {"name": "should_not_run_tool", "arguments": "{}"},
                        },
                    ]
                }
            }
        ]
    }

    pending_tool = PendingConfirmTool()
    blocked_tool = ShouldNotRunTool()
    original_pending = registry.tools.get(pending_tool.name)
    original_blocked = registry.tools.get(blocked_tool.name)
    registry.register(pending_tool)
    registry.register(blocked_tool)
    try:
        service = ChatService(max_iterations=3, max_reflections=1)
        fake_llm = FakeLLM(responses=[[tool_call_chunk]])
        service.llm = fake_llm

        events = await _collect_events(service)
        tool_starts = [event for event in events if event.get("type") == "tool_start"]
        tool_results = [event for event in events if event.get("type") == "tool_result"]
        reflects = [event for event in events if event.get("type") == "reflect"]
        error_events = [event for event in events if event.get("type") == "error"]

        assert len(tool_starts) == 1
        assert len(tool_results) == 1
        assert tool_results[0]["data"]["name"] == "pending_confirm_tool"
        assert tool_results[0]["data"]["result"]["data"]["requires_confirmation"] is True
        assert reflects
        assert reflects[-1]["data"]["action"] == "await_confirmation"
        assert not error_events
        assert blocked_tool.invoked == 0
        assert len(fake_llm.calls) == 1
    finally:
        if original_pending is not None:
            registry.tools[pending_tool.name] = original_pending
        else:
            registry.tools.pop(pending_tool.name, None)
        if original_blocked is not None:
            registry.tools[blocked_tool.name] = original_blocked
        else:
            registry.tools.pop(blocked_tool.name, None)


@pytest.mark.anyio
async def test_chat_service_does_not_false_positive_on_natural_text_with_function_word():
    normal_text_chunk = {
        "choices": [
            {
                "delta": {
                    "content": "这个 SQL 函数(function) 会统计最近 24 小时慢查询数量，并给出趋势解释。",
                }
            }
        ]
    }
    service = ChatService(max_iterations=1, max_reflections=0)
    service.llm = FakeLLM(responses=[[normal_text_chunk]])
    events = await _collect_events(service)
    error_events = [event for event in events if event["type"] == "error"]
    assistant_events = [event for event in events if event["type"] == "assistant"]
    assert not error_events
    assert assistant_events


class _FakeHttpxResponse:
    def __init__(
        self,
        status_code: int,
        *,
        json_data: Any | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.headers = headers or {}

    def json(self) -> Any:
        if self._json_data is None:
            raise ValueError("not json")
        return self._json_data


class _FakeAsyncClient:
    def __init__(self, response: _FakeHttpxResponse) -> None:
        self._response = response

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def request(self, *args: Any, **kwargs: Any) -> _FakeHttpxResponse:
        del args, kwargs
        return self._response


@pytest.mark.anyio
async def test_chat_service_auto_binds_call_service_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ChatService(max_iterations=1, max_reflections=0)
    mock_tool = AsyncMock()
    mock_tool.execute = AsyncMock(return_value=ToolResult(success=True, data={"ok": True}))

    monkeypatch.setattr(service, "_resolve_call_service_binding", lambda datasource_id: (88, None))
    monkeypatch.setattr(
        registry, "get", lambda name: mock_tool if name == "call_praxis_service" else None
    )

    result = await service._execute_tool_lifecycle(
        tool_call={
            "id": "tc-service",
            "function": {
                "name": "call_praxis_service",
                "arguments": '{"method":"GET","path":"/api/v2/ob/clusters"}',
            },
        },
        default_datasource_id=2,
        scope_context=None,
    )

    assert result["result"]["success"] is True
    mock_tool.execute.assert_awaited_once_with(
        service_id=88,
        method="GET",
        path="/api/v2/ob/clusters",
    )


@pytest.mark.anyio
async def test_call_service_tool_treats_html_success_response_as_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.database import Base
    from app.models import models
    from app.tools.registry import CallServiceTool

    db_path = tmp_path / "service.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    db = factory()
    try:
        service = models.Service(
            name="ocp",
            service_type="ocp_api",
            resource_ref="cluster:test",
            status="active",
            config={"host": "127.0.0.1", "port": 8080, "user": "admin", "password": "secret"},
        )
        db.add(service)
        db.commit()
        db.refresh(service)
    finally:
        db.close()

    fake_httpx = Mock()
    fake_httpx.TimeoutException = TimeoutError
    fake_httpx.AsyncClient = lambda timeout=30.0: _FakeAsyncClient(
        _FakeHttpxResponse(
            200,
            json_data=None,
            text="<!DOCTYPE html><html><body>login</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    monkeypatch.setitem(__import__("sys").modules, "httpx", fake_httpx)

    service_id = service.id
    monkeypatch.setattr("app.db.database.SessionLocal", factory)

    tool = CallServiceTool()
    try:
        result = await tool.execute(
            service_id=service_id, method="GET", path="/api/v2/monitor/metric"
        )

        assert result.success is False
        assert result.error["code"] == "unexpected_response_format"
        assert "non-JSON" in result.error["message"]
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_chat_service_returns_structured_error_when_call_service_binding_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ChatService(max_iterations=1, max_reflections=0)
    monkeypatch.setattr(
        service,
        "_resolve_call_service_binding",
        lambda datasource_id: (None, "当前数据源未绑定可用的 PraxisService。"),
    )

    result = await service._execute_tool_lifecycle(
        tool_call={
            "id": "tc-service-missing",
            "function": {
                "name": "call_praxis_service",
                "arguments": '{"method":"GET","path":"/api/v2/ob/clusters"}',
            },
        },
        default_datasource_id=2,
        scope_context=None,
    )

    assert result["error_class"] == "dependency_error"
    assert result["result"]["success"] is False
    assert result["result"]["error"]["code"] == "missing_service_binding"


@pytest.mark.anyio
async def test_chat_service_does_not_apply_domain_metadata_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ChatService(max_iterations=1, max_reflections=0)
    mock_tool = AsyncMock()
    mock_tool.execute = AsyncMock(return_value=ToolResult(success=True, data={"ok": True}))
    monkeypatch.setattr(
        registry, "get", lambda name: mock_tool if name == "call_praxis_service" else None
    )

    result = await service._execute_tool_lifecycle(
        tool_call={
            "id": "tc-service-disabled",
            "function": {
                "name": "call_praxis_service",
                "arguments": '{"service_id":88,"method":"GET","path":"/api/v2/ob/clusters"}',
            },
        },
        default_datasource_id=2,
        scope_context=None,
    )

    assert result["result"]["success"] is True
    mock_tool.execute.assert_awaited_once_with(
        service_id=88,
        method="GET",
        path="/api/v2/ob/clusters",
    )


@pytest.mark.anyio
async def test_chat_service_scope_metadata_is_propagated():
    service = ChatService(max_iterations=1, max_reflections=1)
    service.llm = FakeLLM(
        responses=[
            [
                {
                    "choices": [
                        {
                            "delta": {"content": "scoped reply"},
                        }
                    ]
                }
            ]
        ]
    )
    scope_context = {
        "scope_type": "builder",
        "scope_object_type": "page",
        "scope_object_id": "42",
        "build_session_id": 7,
    }

    events: list[dict] = []
    async for event in service.chat_with_tools(
        messages=[{"role": "user", "content": "build this page"}],
        tools=registry.get_openai_functions(),
        scope_context=scope_context,
        use_state_machine=True,
    ):
        events.append(event)

    assert events
    for event in events:
        meta = event.get("meta") or {}
        assert meta.get("scope_type") == "builder"
        assert meta.get("scope_object_type") == "page"
        assert meta.get("scope_object_id") == "42"
        assert meta.get("build_session_id") == 7


@pytest.mark.anyio
async def test_chat_service_scope_violation_is_reported():
    tool_call_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tc-scope",
                            "function": {
                                "name": "object_operate",
                                "arguments": '{"object_type":"page","action":"publish","object_id":999}',
                            },
                        }
                    ]
                }
            }
        ]
    }
    service = ChatService(max_iterations=2, max_reflections=0)
    service.llm = FakeLLM(responses=[[tool_call_chunk]])
    scope_context = {
        "scope_type": "builder",
        "scope_object_type": "page",
        "scope_object_id": "1",
        "build_session_id": 9,
    }

    events: list[dict] = []
    async for event in service.chat_with_tools(
        messages=[{"role": "user", "content": "publish this page"}],
        tools=registry.get_openai_functions(),
        scope_context=scope_context,
        use_state_machine=True,
    ):
        events.append(event)

    tool_results = [event for event in events if event.get("type") == "tool_result"]
    assert tool_results
    payload = tool_results[0]["data"]["result"]
    assert payload["success"] is False
    assert payload["error"]["code"] == "scope_violation"


@pytest.mark.anyio
async def test_chat_service_forces_finalize_when_iteration_budget_reached():
    tool_call_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tc-finalize",
                            "function": {"name": "unknown_tool", "arguments": "{}"},
                        }
                    ]
                }
            }
        ]
    }
    forced_final_chunk = {
        "choices": [{"delta": {"content": "当前已完成初步分析，建议下一步缩小范围。"}}]
    }
    service = ChatService(max_iterations=1, max_reflections=5)
    service.llm = FakeLLM(responses=[[tool_call_chunk], [forced_final_chunk]])
    events = await _collect_events(service)
    error_events = [event for event in events if event["type"] == "error"]
    assistant_events = [event for event in events if event["type"] == "assistant"]
    assert not error_events
    assert assistant_events
    assert any((event.get("meta") or {}).get("forced_finalize") for event in assistant_events)


@pytest.mark.anyio
async def test_chat_service_detects_repeated_tool_loop():
    repeated_tool_call_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tc-loop",
                            "function": {"name": "unknown_tool", "arguments": '{"sql":"select 1"}'},
                        }
                    ]
                }
            }
        ]
    }
    service = ChatService(max_iterations=5, max_reflections=5)
    service.llm = FakeLLM(
        responses=[
            [repeated_tool_call_chunk],
            [repeated_tool_call_chunk],
            [repeated_tool_call_chunk],
        ]
    )
    events = await _collect_events(service)
    error_events = [event for event in events if event["type"] == "error"]
    assert error_events
    assert error_events[-1]["data"]["error_class"] == "loop_detected"


@pytest.mark.anyio
async def test_chat_service_maps_rate_limited_error_class():
    service = ChatService(max_iterations=1, max_reflections=0)
    service.llm = FakeRateLimitedLLM()
    events = await _collect_events(service)
    error_events = [event for event in events if event["type"] == "error"]
    assert error_events
    assert error_events[-1]["data"]["error_class"] == "rate_limited"


@pytest.mark.anyio
async def test_chat_service_auto_continues_when_finish_reason_length():
    first_round_chunks = [
        {"choices": [{"delta": {"content": "第一段。"}}]},
        {"choices": [{"delta": {}, "finish_reason": "length"}]},
    ]
    continuation_chunks = [
        {"choices": [{"delta": {"content": "第二段补全。"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    service = ChatService(max_iterations=2, max_reflections=0)
    fake_llm = FakeLLM(responses=[first_round_chunks, continuation_chunks])
    service.llm = fake_llm

    events = await _collect_events(service)
    assistant_text = "".join(
        (event.get("data") or {}).get("text", "")
        for event in events
        if event.get("type") == "assistant"
    )

    assert "第一段。第二段补全。" in assistant_text
    assert fake_llm.call_count == 2


@pytest.mark.anyio
async def test_chat_service_tool_event_meta_contains_execution_identifiers():
    tool_call_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tc-meta",
                            "function": {
                                "name": "object_meta_tool",
                                "arguments": '{"object_id":321}',
                            },
                        }
                    ]
                }
            }
        ]
    }
    final_text_chunk = {"choices": [{"delta": {"content": "done"}}]}
    tool = ObjectMetaTool()
    original = registry.tools.get(tool.name)
    registry.register(tool)
    try:
        service = ChatService(max_iterations=2, max_reflections=1)
        service.llm = FakeLLM(responses=[[tool_call_chunk], [final_text_chunk]])
        events = await _collect_events(service)
        tool_result_events = [event for event in events if event.get("type") == "tool_result"]
        assert tool_result_events
        meta = tool_result_events[0].get("meta") or {}
        assert meta.get("object_id") == "321"
        assert meta.get("run_id") == "run-42"
        assert meta.get("release_id") == "9"
        assert isinstance(meta.get("trace_id"), str) and meta.get("trace_id")
    finally:
        if original is not None:
            registry.tools[tool.name] = original
        else:
            registry.tools.pop(tool.name, None)
