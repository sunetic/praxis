from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any

import pytest

from app.services.agent.reasoning_engine import (
    VALID_TRANSITIONS,
    EngineConfig,
    ReasoningEngine,
    ReasoningPhase,
    SimpleToolExecutor,
    _build_retry_system_hint,
    _check_transition,
    _reflector_step,
    _summarize_planning_objectives,
    _tool_signature,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeLLM:
    """Fake LLM that returns pre-configured streaming responses."""

    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.call_count = 0
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> Any:
        del tools, stream, kwargs
        self.calls.append(deepcopy(messages))
        idx = self.call_count
        self.call_count += 1
        if idx >= len(self.responses):
            return
            yield  # pragma: no cover
        for chunk in self.responses[idx]:
            yield chunk


def _text_chunk(text: str, finish_reason: str = "stop") -> dict[str, Any]:
    return {"choices": [{"delta": {"content": text}, "finish_reason": finish_reason}]}


def _tool_call_chunk(
    name: str,
    arguments: str,
    call_id: str = "tc_1",
    index: int = 0,
) -> list[dict[str, Any]]:
    """Generate tool call streaming chunks (start + args)."""
    return [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": index,
                                "id": call_id,
                                "function": {"name": name, "arguments": ""},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": index,
                                "function": {"arguments": arguments},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]


def _make_engine(
    *,
    llm: Any,
    config: EngineConfig | None = None,
    executor: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    compressor: Any | None = None,
) -> ReasoningEngine:
    return ReasoningEngine(
        config=config or EngineConfig(),
        llm=llm,
        tool_executor=SimpleToolExecutor(executor) if executor is not None else None,
        compressor=compressor,
    )


async def _collect(engine: ReasoningEngine, **kwargs: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for event in engine.run(**kwargs):
        events.append(event)
    return events


def test_valid_transitions() -> None:
    assert ReasoningPhase.PLANNING in VALID_TRANSITIONS[ReasoningPhase.THINKING]
    assert ReasoningPhase.DONE not in VALID_TRANSITIONS[ReasoningPhase.THINKING]


def test_check_transition_ok() -> None:
    assert _check_transition(ReasoningPhase.THINKING, ReasoningPhase.PLANNING) == ""


def test_check_transition_invalid() -> None:
    result = _check_transition(ReasoningPhase.THINKING, ReasoningPhase.DONE)
    assert "Invalid transition" in result


def test_tool_signature_deterministic() -> None:
    tool_calls = {0: {"function": {"name": "foo", "arguments": '{"a": 1}'}}}
    assert _tool_signature(tool_calls) == _tool_signature(tool_calls)


def test_tool_signature_different_for_different_calls() -> None:
    tool_calls_1 = {0: {"function": {"name": "foo", "arguments": '{"a": 1}'}}}
    tool_calls_2 = {0: {"function": {"name": "bar", "arguments": '{"a": 1}'}}}
    assert _tool_signature(tool_calls_1) != _tool_signature(tool_calls_2)


def test_reflector_step_continue_on_success() -> None:
    results = [{"name": "t", "result": {"success": True}, "error_class": None}]
    decision = _reflector_step(results, 0, 2)
    assert decision["action"] == "continue"


def test_reflector_step_retry_on_failure() -> None:
    results = [{"name": "t", "result": {"success": False}, "error_class": "schema_error"}]
    decision = _reflector_step(results, 0, 2)
    assert decision["action"] == "retry"


def test_reflector_step_abort_after_repeated_unknown_column_failure() -> None:
    results = [
        {
            "name": "execute_sql",
            "result": {
                "success": False,
                "error": {
                    "code": "sql_execution_error",
                    "category": "unknown_column",
                    "message": "Unknown column 'value' in 'field list'",
                },
            },
            "error_class": "schema_error",
        }
    ]
    decision = _reflector_step(results, 1, 2)
    assert decision["action"] == "abort"
    assert decision["reason_code"] == "schema_recovery_exhausted"


def test_reflector_step_abort_when_reflection_exhausted() -> None:
    results = [{"name": "t", "result": {"success": False}, "error_class": "timeout_error"}]
    decision = _reflector_step(results, 2, 2)
    assert decision["action"] == "abort"


def test_reflector_step_await_confirmation() -> None:
    results = [
        {
            "name": "t",
            "result": {"success": True, "data": {"requires_confirmation": True}},
            "error_class": None,
        }
    ]
    decision = _reflector_step(results, 0, 2)
    assert decision["action"] == "await_confirmation"


def test_build_retry_hint_with_schema_error() -> None:
    results = [
        {
            "name": "execute_sql",
            "error_class": "schema_error",
            "result": {"error": "unknown table orders"},
        }
    ]
    hint = _build_retry_system_hint(results, 1)
    assert "schema" in hint.lower()
    assert "execute_sql" in hint
    assert "must ground on confirmed schema first" in hint
    assert "do not write a new multi-column or joined sql based on guessed fields" in hint.lower()


def test_build_retry_hint_empty_for_success() -> None:
    results = [{"name": "t", "error_class": None, "result": {"success": True}}]
    assert _build_retry_system_hint(results, 1) == ""


def test_summarize_planning_objectives() -> None:
    summary = _summarize_planning_objectives(
        [
            {
                "planning_meta": {
                    "phase": "evidence",
                    "goal": "locate target API doc",
                    "success_criteria": "confirmed path and required params",
                }
            }
        ]
    )
    assert "phase=evidence" in summary
    assert "goal=locate target API doc" in summary
    assert "success=confirmed path and required params" in summary


def test_build_retry_hint_includes_planning_objective_summary() -> None:
    results = [
        {
            "name": "exec_command",
            "error_class": "execution_error",
            "planning_meta": {
                "phase": "evidence",
                "goal": "locate target API doc",
                "success_criteria": "confirmed path and required params",
            },
            "result": {
                "success": False,
                "error": {"code": "missing_path", "message": "path missing"},
            },
        }
    ]
    hint = _build_retry_system_hint(results, 1)
    assert "planning objectives" in hint.lower()
    assert "locate target api doc" in hint.lower()
    assert "confirmed path and required params" in hint.lower()


@pytest.mark.anyio
async def test_simple_text_response() -> None:
    llm = FakeLLM(responses=[[_text_chunk("Hello world!")]])
    engine = _make_engine(llm=llm, config=EngineConfig(max_iterations=5))

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
    )

    event_types = [event["type"] for event in events]
    assert "thinking" in event_types
    assert "assistant" in event_types
    assert "done" in event_types
    assert events[-1]["type"] == "done"

    text_events = [event for event in events if event["type"] == "assistant"]
    assert text_events[0]["data"]["text"] == "Hello world!"


@pytest.mark.anyio
async def test_tool_call_and_continue() -> None:
    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        del name, args
        return {"success": True, "data": {"result": 42}}

    llm = FakeLLM(
        responses=[
            _tool_call_chunk("execute_sql", '{"sql": "SELECT 1"}'),
            [_text_chunk("The result is 42.")],
        ]
    )
    engine = _make_engine(llm=llm, config=EngineConfig(max_iterations=5), executor=executor)

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "run a query"}],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
    )

    event_types = [event["type"] for event in events]
    assert "tool_start" in event_types
    assert "tool_result" in event_types
    assert "reflect" in event_types
    assert "assistant" in event_types
    assert "done" in event_types

    tool_result = next(event for event in events if event["type"] == "tool_result")
    assert tool_result["data"]["result"]["success"] is True


@pytest.mark.anyio
async def test_tool_failure_triggers_retry() -> None:
    call_count = 0

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        del name, args
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"success": False, "error": "unknown table", "error_class": "schema_error"}
        return {"success": True, "data": {"result": 1}}

    llm = FakeLLM(
        responses=[
            _tool_call_chunk("execute_sql", '{"sql": "SELECT * FROM bad"}', call_id="tc1"),
            _tool_call_chunk("execute_sql", '{"sql": "SELECT * FROM good"}', call_id="tc2"),
            [_text_chunk("Done.")],
        ]
    )
    engine = _make_engine(
        llm=llm,
        config=EngineConfig(max_iterations=10, max_reflections=2),
        executor=executor,
    )

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "query"}],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
    )

    reflect_events = [event for event in events if event["type"] == "reflect"]
    assert reflect_events
    assert reflect_events[0]["data"]["action"] == "retry"
    assert events[-1]["type"] == "done"


@pytest.mark.anyio
async def test_batch_boundary_after_forces_reflect_before_dependent_call() -> None:
    executed: list[str] = []

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        executed.append(f"{name}:{args.get('sql') or args.get('path') or ''}")
        return {"success": True, "data": {"ok": True}}

    llm = FakeLLM(
        responses=[
            _tool_call_chunk(
                "exec_command",
                '{"command":"rg","args":["-n","cpu","data/knowledge/2/"],"_runtime":{"batch_boundary_after":true}}',
                call_id="tc-boundary",
            )
            + _tool_call_chunk(
                "call_praxis_service",
                '{"method":"GET","path":"/api/v2/monitor/metric"}',
                call_id="tc-dependent",
                index=1,
            ),
            [_text_chunk("Done.")],
        ]
    )
    engine = _make_engine(
        llm=llm,
        config=EngineConfig(max_iterations=5, max_reflections=1),
        executor=executor,
    )

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "query cpu"}],
        tools=[{"type": "function", "function": {"name": "exec_command"}}],
    )

    tool_starts = [event for event in events if event["type"] == "tool_start"]
    tool_results = [event for event in events if event["type"] == "tool_result"]
    reflect_events = [event for event in events if event["type"] == "reflect"]

    assert len(tool_starts) == 1
    assert len(tool_results) == 1
    assert tool_results[0]["data"]["name"] == "exec_command"
    assert reflect_events
    assert reflect_events[0]["phase"] == "reflecting"
    assert executed == ["exec_command:"]


@pytest.mark.anyio
async def test_engine_executes_only_first_tool_per_round() -> None:
    executed: list[str] = []

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        executed.append(name)
        return {"success": True, "data": {"ok": True}}

    llm = FakeLLM(
        responses=[
            _tool_call_chunk(
                "exec_command", '{"command":"ls","args":["data/"]}', call_id="tc-first"
            )
            + _tool_call_chunk(
                "call_praxis_service",
                '{"method":"GET","path":"/api/v2/monitor/metric"}',
                call_id="tc-second",
                index=1,
            ),
            [_text_chunk("Done.")],
        ]
    )
    engine = _make_engine(
        llm=llm,
        config=EngineConfig(max_iterations=5, max_reflections=1),
        executor=executor,
    )

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "query cpu"}],
        tools=[{"type": "function", "function": {"name": "exec_command"}}],
    )

    tool_starts = [event for event in events if event["type"] == "tool_start"]
    tool_results = [event for event in events if event["type"] == "tool_result"]

    assert len(tool_starts) == 2
    assert len(tool_results) == 2
    assert tool_results[0]["data"]["name"] == "exec_command"
    assert tool_results[1]["data"]["name"] == "call_praxis_service"
    assert executed == ["exec_command", "call_praxis_service"]


@pytest.mark.anyio
async def test_loop_detection() -> None:
    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        del name, args
        return {"success": True, "data": {}}

    repeated_chunk = _tool_call_chunk("execute_sql", '{"sql": "SELECT 1"}')
    llm = FakeLLM(responses=[repeated_chunk, repeated_chunk, repeated_chunk])
    engine = _make_engine(
        llm=llm,
        config=EngineConfig(max_iterations=10, max_repeated_tool_rounds=2),
        executor=executor,
    )

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "test"}],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
    )

    error_events = [event for event in events if event["type"] == "error"]
    assert error_events
    assert error_events[0]["data"]["error_class"] == "loop_detected"


@pytest.mark.anyio
async def test_budget_exhausted_forces_finalize_response() -> None:
    llm = FakeLLM(
        responses=[
            _tool_call_chunk("foo", "{}"),
            _tool_call_chunk("foo2", '{"a": 1}'),
            [_text_chunk("Summary: partial findings.")],
        ]
    )

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        del name, args
        return {"success": True, "data": {}}

    engine = _make_engine(
        llm=llm,
        config=EngineConfig(max_iterations=2, max_progress_bonus=0),
        executor=executor,
    )

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "test"}],
        tools=[{"type": "function", "function": {"name": "foo"}}],
    )

    forced_finalize_events = [
        event
        for event in events
        if event["type"] == "assistant" and event["meta"].get("forced_finalize") is True
    ]
    assert forced_finalize_events
    assert forced_finalize_events[0]["data"]["text"] == "Summary: partial findings."
    assert events[-1]["type"] == "done"


@pytest.mark.anyio
async def test_no_tool_executor() -> None:
    llm = FakeLLM(
        responses=[
            _tool_call_chunk("some_tool", "{}"),
            [_text_chunk("Could not execute tools.")],
        ]
    )
    engine = _make_engine(llm=llm, config=EngineConfig(max_iterations=5))

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "test"}],
        tools=[{"type": "function", "function": {"name": "some_tool"}}],
    )

    tool_results = [event for event in events if event["type"] == "tool_result"]
    assert tool_results
    assert tool_results[0]["data"]["error_class"] == "no_executor"


@pytest.mark.anyio
async def test_reasoning_config_passed_to_llm() -> None:
    captured_kwargs: dict[str, Any] = {}

    class CaptureLLM:
        async def chat(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            stream: bool = True,
            **kwargs: Any,
        ) -> Any:
            del messages, tools, stream
            captured_kwargs.update(kwargs)
            yield _text_chunk("thought deeply")

    engine = _make_engine(
        llm=CaptureLLM(),
        config=EngineConfig(reasoning_config={"enabled": True, "effort": "medium"}),
    )

    await _collect(
        engine,
        messages=[{"role": "user", "content": "think"}],
        tools=[],
    )

    assert "reasoning_config" in captured_kwargs
    assert captured_kwargs["reasoning_config"]["enabled"] is True
    assert captured_kwargs["reasoning_config"]["effort"] == "medium"


@pytest.mark.anyio
async def test_context_compression_triggered() -> None:
    class TrackingCompressor:
        def __init__(self) -> None:
            self.compressed = False

        def should_compress(self, messages: list[dict[str, Any]]) -> bool:
            return len(messages) > 2

        async def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
            self.compressed = True
            return [messages[0], {"role": "system", "content": "[compressed]"}, messages[-1]]

    compressor = TrackingCompressor()
    llm = FakeLLM(responses=[[_text_chunk("done")]])
    engine = _make_engine(
        llm=llm,
        config=EngineConfig(max_iterations=5),
        compressor=compressor,
    )

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]

    events = await _collect(engine, messages=messages, tools=[])

    assert compressor.compressed
    compressed_events = [event for event in events if event["type"] == "context_compressed"]
    assert len(compressed_events) == 1
