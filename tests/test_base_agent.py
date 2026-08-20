from __future__ import annotations

import asyncio
import json
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
    _build_initial_progress_note,
    _build_observation_progress_note,
    _build_plan_progress_note,
    _build_private_revision_context,
    _build_retry_system_hint,
    _build_verification_progress_note,
    _candidate_self_containment_precheck,
    _check_transition,
    _extract_completion_verification_policies,
    _reflector_step,
    _summarize_planning_objectives,
    _tool_signature,
    _verification_requires_new_evidence,
)
from app.services.agent.task_contract import AcceptanceCriterion, TaskContract, latest_user_text
from app.services.agent.task_contract_agent import TaskContractBuild
from app.services.agent.task_runtime import Observation, TaskJournal, VerificationResult


def _failed_sql_observation(
    message: str,
    *,
    category: str,
    error_class: str,
) -> Observation:
    return Observation.from_execution(
        {
            "name": "execute_sql",
            "tool_call_id": "tc-sql-error",
            "arguments": {"sql": "SELECT (SELECT id, name FROM customers)"},
            "result": {
                "success": False,
                "error": {"category": category, "db_message": message},
            },
            "error_class": error_class,
        }
    )


def test_complex_initial_progress_names_the_user_task_without_copying_sql() -> None:
    journal = TaskJournal.create(
        TaskContract(
            objective=(
                "请验证商品经营周报草稿，形成可复核报告。\n"
                "```sql\nSELECT missing_column FROM sample_table\n```\n"
                "最终报告覆盖字段可用性、时间变化和引用异常。"
            ),
            acceptance_criteria=[AcceptanceCriterion(id="ac-1", description="验证商品经营周报")],
            complex=True,
        )
    )

    note = _build_initial_progress_note(journal)

    assert "商品经营周报" in note
    assert "SELECT" not in note
    assert 60 <= len(note) <= 220


def test_verification_progress_explains_unsupported_calculation_naturally() -> None:
    journal = TaskJournal.create(TaskContract(objective="完成数据检查并提供证据"))
    journal.verification = VerificationResult(
        satisfied=False,
        reason="Numeric claims cannot be recomputed from the current evidence.",
        evaluator="llm",
    )

    note = _build_verification_progress_note(journal, satisfied=False)

    assert "系统" not in note
    assert "不能从现有查询结果直接得到" in note
    assert "不做无关查询" in note


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("The returned cardinality is incompatible", "数据结构"),
        ("The tool arguments are malformed", "参数格式不完整"),
    ],
)
def test_progress_notes_explain_known_execution_errors(
    message: str,
    expected: str,
) -> None:
    journal = TaskJournal.create(TaskContract(objective="检查规模和异常"))
    result_shape = "cardinality" in message
    observation = _failed_sql_observation(
        message,
        category="result_shape_error" if result_shape else "argument_error",
        error_class="result_shape_error" if result_shape else "argument_error",
    )

    observation_note = _build_observation_progress_note(journal, [observation], {})
    journal.evaluate_observations(
        [observation],
        iteration=1,
        per_episode_retry_budget=3,
        transient_retry_budget=3,
        max_no_progress_rounds=3,
    )
    plan_note = _build_plan_progress_note(
        journal,
        [
            {
                "name": "execute_sql",
                "arguments": {"sql": "SELECT COUNT(*) FROM customers"},
            }
        ],
        iteration=2,
    )

    assert expected in observation_note
    assert "参数" in plan_note or "数据结构" in plan_note


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeLLM:
    """Fake LLM that returns pre-configured streaming responses."""

    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.call_count = 0
        self.calls: list[list[dict[str, Any]]] = []
        self.call_kwargs: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> Any:
        del tools, stream
        self.calls.append(deepcopy(messages))
        self.call_kwargs.append(deepcopy(kwargs))
        idx = self.call_count
        self.call_count += 1
        if idx >= len(self.responses):
            return
            yield  # pragma: no cover
        for chunk in self.responses[idx]:
            yield chunk


class StaticTaskContractBuilder:
    def __init__(
        self,
        *,
        complex: bool = False,
        high_value: bool = False,
        criteria: list[AcceptanceCriterion] | None = None,
    ) -> None:
        self.complex = complex
        self.high_value = high_value
        self.criteria = criteria or []
        self.calls = 0

    async def build(self, messages: list[dict[str, Any]]) -> TaskContractBuild:
        self.calls += 1
        return TaskContractBuild(
            contract=TaskContract(
                objective=latest_user_text(messages),
                acceptance_criteria=deepcopy(self.criteria),
                complex=self.complex,
                high_value=self.high_value,
            )
        )


def _text_chunk(text: str, finish_reason: str = "stop") -> dict[str, Any]:
    return {"choices": [{"delta": {"content": text}, "finish_reason": finish_reason}]}


def _message_chunk(text: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 19, "completion_tokens": 11},
    }


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
    task_contract_builder: Any | None = None,
) -> ReasoningEngine:
    return ReasoningEngine(
        config=config or EngineConfig(),
        llm=llm,
        tool_executor=SimpleToolExecutor(executor) if executor is not None else None,
        compressor=compressor,
        task_contract_builder=task_contract_builder or StaticTaskContractBuilder(),
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
async def test_invalid_tool_arguments_are_sanitized_before_next_model_round() -> None:
    executed: list[dict[str, Any]] = []

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        del name
        executed.append(args)
        return {"success": True, "data": {"rows": [{"ok": 1}]}}

    llm = FakeLLM(
        responses=[
            _tool_call_chunk("execute_sql", '{"sql":"SELECT broken"', call_id="tc-invalid-json"),
            _tool_call_chunk("execute_sql", '{"sql":"SELECT 1"}', call_id="tc-valid-json"),
            [_text_chunk("Recovered.")],
        ]
    )
    engine = _make_engine(llm=llm, executor=executor)

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "query"}],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
    )

    second_round_tool_history = next(
        message["tool_calls"]
        for message in llm.calls[1]
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    assert json.loads(second_round_tool_history[0]["function"]["arguments"]) == {}
    assert executed == [{"sql": "SELECT 1"}]
    assert events[-1]["data"]["completed"] is True


@pytest.mark.anyio
async def test_independent_recoverable_failures_have_independent_retry_budgets() -> None:
    executed_sql: list[str] = []

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        assert name == "execute_sql"
        sql = str(args.get("sql") or "")
        executed_sql.append(sql)

        if "customer_email" in sql:
            return {
                "success": False,
                "error": {
                    "code": "sql_execution_error",
                    "category": "unknown_column",
                    "db_errno": 1054,
                    "db_message": "Unknown column 'customer_email' in 'field list'",
                    "message": "Unknown column 'customer_email' in 'field list'",
                    "retry_hint": "Discover available schema objects first, then adapt SQL.",
                },
                "error_class": "schema_error",
            }
        if "DATE_TRUNC" in sql:
            return {
                "success": False,
                "error": {
                    "code": "sql_execution_error",
                    "category": "execution_error",
                    "db_errno": 1305,
                    "db_message": "FUNCTION praxis_test.DATE_TRUNC does not exist",
                    "message": "FUNCTION praxis_test.DATE_TRUNC does not exist",
                },
                "error_class": "execution_error",
            }
        if "eval_order_lines" in sql:
            return {
                "success": False,
                "error": {
                    "code": "sql_execution_error",
                    "category": "unknown_table",
                    "db_errno": 1146,
                    "db_message": "Table 'praxis_test.eval_order_lines' does not exist",
                    "message": "Table 'praxis_test.eval_order_lines' does not exist",
                    "retry_hint": "Discover available schema objects first, then adapt SQL.",
                },
                # Mirrors the current ChatService classifier, which does not consume category.
                "error_class": "execution_error",
            }
        return {"success": True, "data": {"rows": [{"ok": 1}]}, "error_class": "none"}

    llm = FakeLLM(
        responses=[
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT customer_email FROM eval_customers",'
                '"intent":"phase-a expected column failure"}',
                call_id="tc-column-failure",
            ),
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"DESCRIBE eval_customers","intent":"phase-a schema discovery"}',
                call_id="tc-customer-schema",
            ),
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT email, COUNT(*) FROM eval_customers GROUP BY email",'
                '"intent":"phase-a corrected query"}',
                call_id="tc-customer-corrected",
            ),
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT DATE_TRUNC(created_at) FROM eval_orders",'
                '"intent":"phase-b expected dialect failure"}',
                call_id="tc-dialect-failure",
            ),
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT DATE(created_at) FROM eval_orders",'
                '"intent":"phase-b corrected query"}',
                call_id="tc-dialect-corrected",
            ),
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT order_id FROM eval_order_lines",'
                '"intent":"phase-c expected table failure"}',
                call_id="tc-table-failure",
            ),
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SHOW TABLES","intent":"phase-c schema discovery"}',
                call_id="tc-table-discovery",
            ),
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT order_id FROM eval_order_items GROUP BY order_id",'
                '"intent":"phase-c corrected query"}',
                call_id="tc-table-corrected",
            ),
            [_text_chunk("All three independent failures recovered; audit complete.")],
        ]
    )
    engine = _make_engine(
        llm=llm,
        config=EngineConfig(max_iterations=20, max_reflections=2),
        executor=executor,
    )

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "Run the three-stage recovery audit."}],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
    )

    recovery_actions = [
        event["data"]["action"]
        for event in events
        if event["type"] == "reflect" and event["data"]["action"] != "continue"
    ]
    assistant_text = "".join(
        event["data"].get("text", "") for event in events if event["type"] == "assistant"
    )

    assert recovery_actions == ["retry", "retry", "retry"]
    assert len(executed_sql) == 8
    assert executed_sql[-2:] == [
        "SHOW TABLES",
        "SELECT order_id FROM eval_order_items GROUP BY order_id",
    ]
    assert "audit complete" in assistant_text
    assert events[-1]["type"] == "done"


@pytest.mark.anyio
async def test_complex_task_withholds_incomplete_candidate_until_verifier_passes() -> None:
    executed_sql: list[str] = []

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        assert name == "execute_sql"
        executed_sql.append(str(args.get("sql") or ""))
        return {"success": True, "data": {"rows": [{"checked": True}]}}

    llm = FakeLLM(
        responses=[
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT COUNT(*) AS checked FROM eval_customers"}',
                call_id="tc-customers",
            ),
            [_text_chunk("客户检查完成，任务全部成功。")],
            [
                _text_chunk(
                    '{"satisfied":false,"reason":"订单检查缺失",'
                    '"missing":["ac-2 order audit"],"contradictions":[],"criterion_results":[]}'
                )
            ],
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT COUNT(*) AS checked FROM eval_orders"}',
                call_id="tc-orders",
            ),
            [_text_chunk("客户与订单检查均已完成，并附有查询证据。")],
            [
                _text_chunk(
                    '{"satisfied":true,"reason":"all criteria covered","missing":[],'
                    '"contradictions":[],"criterion_results":['
                    '{"id":"ac-1","satisfied":true,"evidence_refs":["tc-customers"]},'
                    '{"id":"ac-2","satisfied":true,"evidence_refs":["tc-orders"]}]}'
                )
            ],
        ]
    )
    engine = _make_engine(
        llm=llm,
        config=EngineConfig(max_iterations=8, max_verification_retries=2),
        executor=executor,
        task_contract_builder=StaticTaskContractBuilder(
            complex=True,
            criteria=[
                AcceptanceCriterion(id="ac-1", description="检查客户表并提供证据"),
                AcceptanceCriterion(id="ac-2", description="检查订单表并提供证据"),
            ],
        ),
    )

    events = await _collect(
        engine,
        messages=[
            {
                "role": "user",
                "content": "请完成审计：\n1. 检查客户表并提供证据\n2. 检查订单表并提供证据",
            }
        ],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
    )

    visible_text = "".join(
        event["data"].get("text", "") for event in events if event["type"] == "assistant"
    )
    verifications = [event["data"] for event in events if event["type"] == "verification"]
    done = events[-1]

    assert executed_sql == [
        "SELECT COUNT(*) AS checked FROM eval_customers",
        "SELECT COUNT(*) AS checked FROM eval_orders",
    ]
    assert "客户检查完成，任务全部成功" not in visible_text
    assert visible_text == "客户与订单检查均已完成，并附有查询证据。"
    assert [item["satisfied"] for item in verifications] == [False, True]
    assert done["data"]["completed"] is True
    assert done["data"]["status"] == "completed"


@pytest.mark.anyio
async def test_verifier_semantic_rejection_rewrites_without_forced_tool_busywork() -> None:
    executed_actions: list[str] = []

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        assert name == "call_praxis_service"
        executed_actions.append(str(args.get("path") or ""))
        return {"success": True, "data": {"status": "ok", "uptime_seconds": 3600}}

    llm = FakeLLM(
        responses=[
            _tool_call_chunk(
                "call_praxis_service",
                '{"service_id":1,"method":"GET","path":"/health","intent":"确认服务当前是否可用"}',
                call_id="tc-health",
            ),
            [_text_chunk("服务当前可用，因此长期运行没有风险。")],
            [
                _text_chunk(
                    '{"satisfied":false,"reason":"single health check does not establish long-term risk",'
                    '"missing":[],"contradictions":["long-term reliability is not established"],'
                    '"criterion_results":[{"id":"ac-2","satisfied":false}]}'
                )
            ],
            [_text_chunk("本次检查确认服务当前可用；长期运行风险仍需历史数据才能判断。")],
            [
                _text_chunk(
                    '{"satisfied":true,"reason":"supported wording",'
                    '"missing":[],"contradictions":[],"criterion_results":[]}'
                )
            ],
        ]
    )
    engine = _make_engine(
        llm=llm,
        config=EngineConfig(max_iterations=6, max_verification_retries=2),
        executor=executor,
        task_contract_builder=StaticTaskContractBuilder(
            complex=True,
            criteria=[
                AcceptanceCriterion(id="ac-1", description="检查当前可用性"),
                AcceptanceCriterion(id="ac-2", description="说明运行风险"),
            ],
        ),
    )

    events = await _collect(
        engine,
        messages=[
            {
                "role": "user",
                "content": "请完成服务检查：\n1. 检查当前可用性\n2. 说明运行风险",
            }
        ],
        tools=[{"type": "function", "function": {"name": "call_praxis_service"}}],
    )

    assert executed_actions == ["/health"]
    assert llm.call_kwargs[3].get("tool_choice") != "required"
    rewrite_messages = llm.calls[3]
    assert any(
        item.get("role") == "system"
        and "<rejected_draft>" in str(item.get("content") or "")
        and "长期运行没有风险" in str(item.get("content") or "")
        and "Completion verification failed" in str(item.get("content") or "")
        for item in rewrite_messages
    )
    progress_notes = [
        event["data"]["text"] for event in events if event["type"] == "assistant_progress"
    ]
    assert any("确认服务当前是否可用" in note for note in progress_notes)
    assert any("说明运行风险" in note and "现有证据" in note for note in progress_notes)
    assert events[-1]["data"]["completed"] is True


@pytest.mark.anyio
async def test_tool_task_emits_task_plan_then_model_transition_before_tool() -> None:
    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        del name, args
        return {"success": True, "data": {"status": "ok"}}

    narration = (
        "我先确认当前服务的实时状态，这一步能直接判断它现在是否可用；拿到结果后我会用一句话说明。"
    )
    llm = FakeLLM(
        responses=[
            [
                _text_chunk(narration, finish_reason=None),
                *_tool_call_chunk(
                    "call_praxis_service",
                    '{"service_id":1,"method":"GET","path":"/health",'
                    '"intent":"确认服务当前是否可用"}',
                    call_id="tc-visible-plan",
                ),
            ],
            [_text_chunk("服务当前可用。")],
        ]
    )
    engine = _make_engine(llm=llm, executor=executor)

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "请检查当前服务是否可用。"}],
        tools=[{"type": "function", "function": {"name": "call_praxis_service"}}],
    )

    progress_indexes = [
        index for index, event in enumerate(events) if event["type"] == "assistant_progress"
    ]
    tool_start_index = next(
        index for index, event in enumerate(events) if event["type"] == "tool_start"
    )
    progress_notes = [events[index]["data"]["text"] for index in progress_indexes]
    assert progress_notes[0] == (
        "我先确认实际范围和可用信息，再用最直接的查询取得证据，然后给你简洁结论。"
    )
    assert progress_notes[1] == narration
    assert progress_indexes[0] < progress_indexes[1] < tool_start_index
    system_prompts = [message["content"] for message in llm.calls[0] if message["role"] == "system"]
    assert any("Visible action narration" in prompt for prompt in system_prompts)
    assert any(
        "Evidence discipline for every tool-backed task" in prompt for prompt in system_prompts
    )
    assert any("make each material claim traceable" in prompt for prompt in system_prompts)
    assert not any("missing schema dimension" in prompt for prompt in system_prompts)
    assert events[-1]["data"]["completed"] is True


@pytest.mark.anyio
async def test_complex_task_never_reports_success_when_verification_stays_incomplete() -> None:
    llm = FakeLLM(
        responses=[
            [_text_chunk("所有阶段均已成功完成。")],
            [
                _text_chunk(
                    '{"satisfied":false,"reason":"缺少真实工具证据",'
                    '"missing":["执行阶段一","执行阶段二"]}'
                )
            ],
            [_text_chunk("现在真的全部成功了。")],
            [
                _text_chunk(
                    '{"satisfied":false,"reason":"仍无工具证据",'
                    '"missing":["执行阶段一","执行阶段二"]}'
                )
            ],
        ]
    )
    engine = _make_engine(
        llm=llm,
        config=EngineConfig(max_iterations=5, max_verification_retries=2),
        task_contract_builder=StaticTaskContractBuilder(
            complex=True,
            criteria=[
                AcceptanceCriterion(id="ac-1", description="完成阶段一并验证"),
                AcceptanceCriterion(id="ac-2", description="完成阶段二并验证"),
            ],
        ),
    )

    events = await _collect(
        engine,
        messages=[
            {
                "role": "user",
                "content": "请执行：\n1. 完成阶段一并验证\n2. 完成阶段二并验证",
            }
        ],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
    )

    visible_text = "".join(
        event["data"].get("text", "") for event in events if event["type"] == "assistant"
    )
    assert "所有阶段均已成功完成" not in visible_text
    assert "现在真的全部成功了" not in visible_text
    assert "还有几处证据不够扎实" in visible_text
    assert "标记为成功" not in visible_text
    assert "执行状态已保存" not in visible_text
    assert any(event["type"] == "checkpoint" for event in events)
    assert events[-1]["data"]["completed"] is False
    assert events[-1]["data"]["status"] == "incomplete"


@pytest.mark.anyio
async def test_verification_can_exceed_global_attempt_limit_while_evidence_grows() -> None:
    executed_sql: list[str] = []

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        assert name == "execute_sql"
        executed_sql.append(str(args.get("sql") or ""))
        return {"success": True, "data": {"rows": [{"checked": True}]}}

    llm = FakeLLM(
        responses=[
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT COUNT(*) FROM eval_orders","intent":"核对基础数量"}',
                call_id="tc-orders",
            ),
            [_text_chunk("初稿。")],
            [
                _text_chunk(
                    '{"satisfied":false,"reason":"缺少金额证据","missing":["金额"],"repair_type":"new_evidence"}'
                )
            ],
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT SUM(total_amount) FROM eval_orders","intent":"核对汇总值"}',
                call_id="tc-amount",
            ),
            [_text_chunk("第二稿。")],
            [
                _text_chunk(
                    '{"satisfied":false,"reason":"缺少状态证据","missing":["状态"],"repair_type":"new_evidence"}'
                )
            ],
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT status, COUNT(*) FROM eval_orders GROUP BY status",'
                '"intent":"核对分类分布"}',
                call_id="tc-status",
            ),
            [_text_chunk("订单数量和金额均已通过查询验证。")],
            [_text_chunk('{"satisfied":true,"reason":"证据完整","missing":[]}')],
        ]
    )
    engine = _make_engine(
        llm=llm,
        config=EngineConfig(max_iterations=8, max_verification_retries=1),
        executor=executor,
        task_contract_builder=StaticTaskContractBuilder(
            complex=True,
            criteria=[
                AcceptanceCriterion(id="ac-1", description="核对订单数量"),
                AcceptanceCriterion(id="ac-2", description="核对订单金额"),
            ],
        ),
    )

    events = await _collect(
        engine,
        messages=[
            {
                "role": "user",
                "content": "请完成订单审计：\n1. 核对订单数量\n2. 核对订单金额",
            }
        ],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
    )

    verifications = [event["data"] for event in events if event["type"] == "verification"]
    assert executed_sql == [
        "SELECT COUNT(*) FROM eval_orders",
        "SELECT SUM(total_amount) FROM eval_orders",
        "SELECT status, COUNT(*) FROM eval_orders GROUP BY status",
    ]
    assert [item["satisfied"] for item in verifications] == [False, False, True]
    assert [item["no_progress_rounds"] for item in verifications] == [0, 0, 0]
    assert llm.call_kwargs[3]["tool_choice"] == "required"
    assert llm.call_kwargs[6]["tool_choice"] == "required"
    progress_notes = [
        event["data"]["text"] for event in events if event["type"] == "assistant_progress"
    ]
    assert len(progress_notes[0]) >= 80
    assert "核对订单数量" in progress_notes[0]
    assert "核对订单金额" in progress_notes[0]
    assert "直接证据" in progress_notes[0]
    assert any("核对汇总值" in note for note in progress_notes)
    assert any("缺少直接证据" in note for note in progress_notes)
    assert progress_notes[-1] == "复核已经通过，关键结论都有对应证据。我正在整理最终报告。"
    assert events[-1]["data"]["completed"] is True


@pytest.mark.anyio
async def test_simple_contract_skips_completion_verifier() -> None:
    llm = FakeLLM(responses=[[_text_chunk("2")]])
    engine = _make_engine(llm=llm)

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "1+1 等于多少？"}],
        tools=[],
    )

    assert llm.call_count == 1
    assert not [event for event in events if event["type"] == "task_contract"]
    assert not [event for event in events if event["type"] == "verification"]
    assert events[-1]["data"]["completed"] is True


@pytest.mark.anyio
async def test_engine_builds_task_contract_before_planning_and_counts_usage() -> None:
    contract_response = {
        "constraints": [],
        "acceptance_criteria": [
            {
                "description": "Answer the request",
                "required": True,
                "requires_tool_evidence": False,
                "required_tool_outcome": "any",
                "component_hints": [],
                "source_excerpt": "Handle the request.",
            }
        ],
        "output_requirements": [],
        "complex": False,
        "high_value": False,
    }
    llm = FakeLLM(
        responses=[
            [_message_chunk(json.dumps(contract_response))],
            [_text_chunk("done")],
        ]
    )
    engine = ReasoningEngine(llm=llm)

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "Handle the request."}],
        tools=[],
    )

    state = next(event["data"] for event in events if event["type"] == "task_state")
    assert llm.call_count == 2
    assert state["contract"]["acceptance_criteria"][0]["description"] == "Answer the request"
    assert state["metrics"]["llm_calls"] == 2
    assert state["metrics"]["input_tokens"] == 19
    assert state["metrics"]["output_tokens"] == 11
    assert events[-1]["data"]["completed"] is True


@pytest.mark.anyio
async def test_resumed_task_reuses_persisted_contract_without_reclassification() -> None:
    builder = StaticTaskContractBuilder(complex=True)
    saved = TaskJournal.create(
        TaskContract(
            objective="Persisted objective",
            acceptance_criteria=[AcceptanceCriterion(id="ac-1", description="Persisted outcome")],
        )
    ).to_dict()
    llm = FakeLLM(responses=[[_text_chunk("resumed")]])
    engine = _make_engine(llm=llm, task_contract_builder=builder)

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "continue"}],
        tools=[],
        task_state=saved,
    )

    state = next(event["data"] for event in events if event["type"] == "task_state")
    assert builder.calls == 0
    assert state["contract"]["objective"] == "Persisted objective"
    assert state["contract"]["acceptance_criteria"][0]["description"] == "Persisted outcome"
    assert events[-1]["data"]["completed"] is True


@pytest.mark.anyio
async def test_simple_task_cannot_claim_completion_with_unresolved_failure() -> None:
    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        del name, args
        return {
            "success": False,
            "error": {
                "category": "unknown_column",
                "db_errno": 1054,
                "db_message": "Unknown column 'missing' in 'field list'",
            },
            "error_class": "schema_error",
        }

    llm = FakeLLM(
        responses=[
            _tool_call_chunk(
                "execute_sql", '{"sql":"SELECT missing FROM eval_orders"}', call_id="tc-missing"
            ),
            [_text_chunk("查询已经成功完成。")],
            [_text_chunk("现在可以宣布成功。")],
            [_text_chunk("第三次仍然没有修复查询。")],
        ]
    )
    engine = _make_engine(
        llm=llm,
        config=EngineConfig(max_verification_retries=2),
        executor=executor,
    )

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "查询一个字段"}],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
    )

    visible = "".join(
        event["data"].get("text", "") for event in events if event["type"] == "assistant"
    )
    verifications = [event["data"] for event in events if event["type"] == "verification"]
    assert "查询已经成功完成" not in visible
    assert "现在可以宣布成功" not in visible
    assert "第三次仍然没有修复查询" not in visible
    assert "本次执行尚未完成" in visible
    assert "已保留 1 条工具证据" in visible
    assert verifications
    assert all(item["evaluator"] == "failure_episode_audit" for item in verifications)
    assert events[-1]["data"]["completed"] is False


@pytest.mark.anyio
async def test_high_value_task_can_require_adversarial_verification() -> None:
    llm = FakeLLM(
        responses=[
            [_text_chunk("生产安全审计完成，未发现问题。")],
            [_text_chunk('{"satisfied":true,"reason":"primary pass","missing":[]}')],
            [_text_chunk('{"satisfied":true,"reason":"arithmetic pass","missing":[]}')],
            [
                _text_chunk(
                    '{"satisfied":true,"reason":"adversarial pass",'
                    '"missing":[],"contradictions":[]}'
                )
            ],
        ]
    )
    engine = _make_engine(
        llm=llm,
        config=EngineConfig(adversarial_verification_enabled=True),
        task_contract_builder=StaticTaskContractBuilder(complex=True, high_value=True),
    )

    events = await _collect(
        engine,
        messages=[
            {
                "role": "user",
                "content": "请执行生产安全审计：\n1. 检查权限边界\n2. 输出审计报告",
            }
        ],
        tools=[],
    )

    verification = next(event["data"] for event in events if event["type"] == "verification")
    assert llm.call_count == 4
    assert verification["satisfied"] is True
    assert verification["evaluator"] == "llm+arithmetic_llm+adversarial_llm"
    assert events[-1]["data"]["completed"] is True


@pytest.mark.anyio
async def test_complex_final_candidate_cannot_reference_hidden_failed_draft() -> None:
    llm = FakeLLM(responses=[])
    engine = _make_engine(llm=llm)
    journal = TaskJournal.create(
        TaskContract(
            objective="完成检查并输出完整报告",
            complex=True,
        )
    )
    journal.metrics.verification_attempts = 1

    result = await engine._verify_candidate(
        journal,
        "Updated impact: 89.90. All other findings in the original report remain accurate.",
    )

    assert result.satisfied is False
    assert result.evaluator == "deterministic_candidate"
    assert "self-contained" in result.missing[0]
    assert llm.call_count == 0


def test_revision_reply_to_internal_feedback_is_not_a_final_answer() -> None:
    journal = TaskJournal.create(TaskContract(objective="完成检查并输出完整报告", complex=True))
    journal.metrics.verification_attempts = 2

    result = _candidate_self_containment_precheck(
        journal,
        "您的复核结论非常准确和全面！让我确认一下最终版本应该如何调整。",
    )

    assert result is not None
    assert result.evaluator == "deterministic_candidate"


def test_self_containment_defect_rewrites_without_more_tool_calls() -> None:
    verification = VerificationResult(
        satisfied=False,
        reason="Candidate is not a self-contained final report.",
        missing=["Rewrite and restate the complete report."],
        evaluator="llm",
    )

    assert _verification_requires_new_evidence(verification) is False
    context = _build_private_revision_context("旧草稿", "请重写")
    assert "not a user message" in context
    assert "<rejected_draft>\n旧草稿" in context


def test_skill_verification_policies_are_extracted_as_opaque_extensions() -> None:
    prompt = (
        "Base agent instructions.\n"
        "<completion_verification_policy>\n"
        "Validate domain claims against the authoritative source.\n"
        "Keep inferred conclusions labelled.\n"
        "</completion_verification_policy>\n"
        "<completion_verification_policy>Require an explicit rubric for ratings."
        "</completion_verification_policy>"
    )

    assert _extract_completion_verification_policies(prompt) == [
        "Validate domain claims against the authoritative source.\nKeep inferred conclusions labelled.",
        "Require an explicit rubric for ratings.",
    ]


@pytest.mark.anyio
async def test_runtime_records_llm_calls_tokens_and_elapsed_time() -> None:
    llm = FakeLLM(
        responses=[
            [
                {
                    "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                }
            ]
        ]
    )
    engine = _make_engine(llm=llm)

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "simple"}],
        tools=[],
    )

    final_state = [event["data"] for event in events if event["type"] == "task_state"][-1]
    metrics = final_state["metrics"]
    assert metrics["llm_calls"] == 1
    assert metrics["input_tokens"] == 12
    assert metrics["output_tokens"] == 3
    assert metrics["elapsed_ms"] >= 0


@pytest.mark.anyio
async def test_transient_failures_use_exponential_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.services.agent.reasoning_engine.asyncio.sleep", fake_sleep)
    attempts = 0

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        del name, args
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return {
                "success": False,
                "error": {"category": "timeout", "message": "query timed out"},
                "error_class": "timeout_error",
            }
        return {"success": True, "data": {"rows": [{"ok": 1}]}}

    llm = FakeLLM(
        responses=[
            _tool_call_chunk("execute_sql", '{"sql":"SELECT 1"}', call_id="tc-timeout"),
            _tool_call_chunk("execute_sql", '{"sql":"SELECT 1"}', call_id="tc-retry"),
            [_text_chunk("done")],
        ]
    )
    engine = _make_engine(
        llm=llm,
        config=EngineConfig(
            transient_backoff_base_seconds=0.25,
            transient_backoff_max_seconds=1.0,
        ),
        executor=executor,
    )

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "query"}],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
    )

    transient_progress = next(
        event["data"]
        for event in events
        if event["type"] == "progress" and event["data"]["decision"] == "transient_failure"
    )
    assert transient_progress["retry_after_seconds"] == 0.25
    assert sleeps == [0.25]
    assert events[-1]["data"]["completed"] is True


@pytest.mark.anyio
async def test_global_elapsed_time_limit_checkpoint_is_resumable() -> None:
    llm = FakeLLM(responses=[])
    engine = _make_engine(
        llm=llm,
        config=EngineConfig(max_elapsed_seconds=0.000000001),
        task_contract_builder=StaticTaskContractBuilder(
            complex=True,
            criteria=[
                AcceptanceCriterion(id="ac-1", description="检查客户数据"),
                AcceptanceCriterion(id="ac-2", description="检查订单数据"),
            ],
        ),
    )

    events = await _collect(
        engine,
        messages=[
            {
                "role": "user",
                "content": "完成检查：\n1. 检查客户数据\n2. 检查订单数据",
            }
        ],
        tools=[],
    )

    checkpoint = next(event for event in events if event["type"] == "checkpoint")
    assert checkpoint["data"]["reason_code"] == "time_limit"
    assert checkpoint["data"]["resumable"] is True
    assert checkpoint["data"]["remaining_work"] == ["检查客户数据", "检查订单数据"]
    assert events[-1]["data"]["completed"] is False
    assert events[-1]["data"]["status"] == "incomplete"


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
async def test_independent_read_only_sql_calls_execute_in_parallel() -> None:
    active = 0
    max_active = 0

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        del name, args
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {"success": True, "data": {"rows": [{"ok": 1}]}}

    llm = FakeLLM(
        responses=[
            _tool_call_chunk(
                "execute_sql", '{"sql":"SELECT COUNT(*) FROM customers"}', call_id="tc-a"
            )
            + _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT COUNT(*) FROM orders"}',
                call_id="tc-b",
                index=1,
            ),
            [_text_chunk("两项只读检查完成。")],
        ]
    )
    engine = _make_engine(llm=llm, executor=executor)

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "并行检查两个计数"}],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
    )

    results = [event for event in events if event["type"] == "tool_result"]
    assert max_active == 2
    assert len(results) == 2
    assert all(event["data"]["parallel"] is True for event in results)
    assert events[-1]["data"]["completed"] is True


@pytest.mark.anyio
async def test_write_or_mixed_tool_batch_remains_sequential() -> None:
    active = 0
    max_active = 0

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        del name, args
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"success": True, "data": {"affected_rows": 1}}

    llm = FakeLLM(
        responses=[
            _tool_call_chunk(
                "execute_sql", '{"sql":"UPDATE customers SET active=1"}', call_id="tc-write"
            )
            + _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT COUNT(*) FROM customers"}',
                call_id="tc-read",
                index=1,
            ),
            [_text_chunk("操作完成。")],
        ]
    )
    engine = _make_engine(llm=llm, executor=executor)

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "执行混合操作"}],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
    )

    results = [event for event in events if event["type"] == "tool_result"]
    assert max_active == 1
    assert len(results) == 2
    assert all(event["data"]["parallel"] is False for event in results)


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
    context_states = [
        event["data"]["state"]
        for event in events
        if event["type"] == "context_status" and event["data"].get("state")
    ]
    assert context_states[:2] == ["compressing", "ready"]


@pytest.mark.anyio
async def test_checkpoint_can_resume_after_engine_restart_with_failure_history() -> None:
    async def failing_executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        del name, args
        return {
            "success": False,
            "error": {
                "code": "sql_execution_error",
                "category": "unknown_table",
                "db_errno": 1146,
                "db_message": "Table 'db.missing_orders' does not exist",
            },
            "error_class": "schema_error",
        }

    first_llm = FakeLLM(
        responses=[
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT * FROM missing_orders"}',
                call_id="tc-missing",
            ),
            [_text_chunk("阶段性结论：表不存在，等待续跑。")],
        ]
    )
    first_engine = _make_engine(
        llm=first_llm,
        config=EngineConfig(max_iterations=1, max_progress_bonus=0),
        executor=failing_executor,
    )
    first_events = await _collect(
        first_engine,
        messages=[{"role": "user", "content": "审计订单数据"}],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
    )
    checkpoint_state = [event["data"] for event in first_events if event["type"] == "task_state"][
        -1
    ]

    executed_sql: list[str] = []

    async def recovery_executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        del name
        sql = str(args.get("sql") or "")
        executed_sql.append(sql)
        return {"success": True, "data": {"rows": [{"table": "eval_orders"}]}}

    second_llm = FakeLLM(
        responses=[
            _tool_call_chunk("execute_sql", '{"sql":"SHOW TABLES"}', call_id="tc-discover"),
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT COUNT(*) FROM eval_orders"}',
                call_id="tc-corrected",
            ),
            [_text_chunk("已从检查点恢复，订单审计完成。")],
        ]
    )
    second_engine = _make_engine(
        llm=second_llm,
        config=EngineConfig(max_iterations=5),
        executor=recovery_executor,
    )
    resumed_events = await _collect(
        second_engine,
        messages=[{"role": "user", "content": "继续执行"}],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
        task_state=checkpoint_state,
    )

    final_state = [event["data"] for event in resumed_events if event["type"] == "task_state"][-1]
    first_resume_context = "\n".join(
        str(message.get("content") or "") for message in second_llm.calls[0]
    )
    assert final_state["task_run_id"] == checkpoint_state["task_run_id"]
    assert final_state["metrics"]["resumptions"] == 1
    assert final_state["failure_episodes"][0]["status"] == "resolved"
    assert final_state["failure_episodes"][0]["attempts"] == 1
    assert "missing_orders" in first_resume_context
    assert "Do not repeat a failed strategy" in first_resume_context
    assert executed_sql == ["SHOW TABLES", "SELECT COUNT(*) FROM eval_orders"]
    assert resumed_events[-1]["data"]["completed"] is True


@pytest.mark.anyio
async def test_resume_correction_is_included_in_completion_verification() -> None:
    correction = "继续执行，并提供订单总数和可追溯的查询证据"
    llm = FakeLLM(
        responses=[
            [_text_chunk("订单总数为 13，并已附查询证据。")],
            [
                _text_chunk(
                    '{"satisfied":true,"reason":"correction satisfied","missing":[],'
                    '"contradictions":[],"criterion_results":[]}'
                )
            ],
        ]
    )
    engine = _make_engine(llm=llm, config=EngineConfig(max_iterations=2))
    task_state = {
        "version": 1,
        "task_run_id": "resume-correction",
        "status": "checkpointed",
        "contract": {
            "objective": "统计订单",
            "constraints": [],
            "acceptance_criteria": [],
            "output_requirements": [],
            "complex": False,
            "high_value": False,
        },
        "steps": [],
        "evidence": [],
        "failure_episodes": [],
        "metrics": {},
    }

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": correction}],
        tools=[],
        task_state=task_state,
    )

    verifier_prompt = "\n".join(str(item.get("content") or "") for item in llm.calls[1])
    assert correction in verifier_prompt
    assert "USER CORRECTIONS" in verifier_prompt
    assert [event for event in events if event["type"] == "verification"]
    assert events[-1]["data"]["completed"] is True


@pytest.mark.anyio
async def test_context_compression_reinjects_structured_failure_history() -> None:
    class DropHistoryCompressor:
        def __init__(self) -> None:
            self.compressed = False

        def should_compress(self, messages: list[dict[str, Any]]) -> bool:
            return not self.compressed and any(
                message.get("role") == "tool" for message in messages
            )

        async def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
            self.compressed = True
            return [messages[0], messages[-1]]

    calls = 0

    async def executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
        del name, args
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "success": False,
                "error": {
                    "category": "unknown_column",
                    "db_errno": 1054,
                    "db_message": "Unknown column 'customer_email' in 'field list'",
                },
                "error_class": "schema_error",
            }
        return {"success": True, "data": {"rows": [{"email": "a@example.com"}]}}

    compressor = DropHistoryCompressor()
    llm = FakeLLM(
        responses=[
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT customer_email FROM eval_customers"}',
                call_id="tc-bad-column",
            ),
            _tool_call_chunk(
                "execute_sql",
                '{"sql":"SELECT email FROM eval_customers"}',
                call_id="tc-good-column",
            ),
            [_text_chunk("检查完成。")],
        ]
    )
    engine = _make_engine(llm=llm, executor=executor, compressor=compressor)

    events = await _collect(
        engine,
        messages=[{"role": "user", "content": "检查客户邮箱"}],
        tools=[{"type": "function", "function": {"name": "execute_sql"}}],
    )

    compressed_context = "\n".join(str(message.get("content") or "") for message in llm.calls[1])
    assert compressor.compressed is True
    assert "failure_history" in compressed_context
    assert "unknown_column" in compressed_context
    assert "attempted_strategies" in compressed_context
    assert any(event["type"] == "context_compressed" for event in events)
    assert events[-1]["data"]["completed"] is True
