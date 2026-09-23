import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from test_models import config

from app.services.agent.context import (
    FALLBACK_LABEL,
    MEMORY_LABEL,
    message_groups,
    protected_indices,
    summary_references,
    valid_summary,
)
from app.services.agent.definitions import AgentDefinition
from app.services.agent.models import ModelFactory
from app.services.agent.runtime import ExecutionBudget
from app.services.agent.service import AgentRunService


def seed_history(store):
    definition = AgentDefinition(
        name="context-test",
        tool_names=frozenset(),
        instructions="Follow the latest user correction.",
    )
    row = store.submit("conversation", "user", "seed", "original", definition)
    store.claim(row["id"], "seed-owner")
    history = []
    for index in range(10):
        prompt = (
            "原始目标：调查 orders，未经批准不得写入。"
            if index == 0
            else f"历史问题 {index}: " + "historical detail " * 55
        )
        history += [
            ModelRequest(parts=[UserPromptPart(prompt)]),
            ModelResponse(parts=[TextPart(f"历史记录 {index}: " + "observed detail " * 20)]),
        ]
    store.finish(
        row["id"],
        "seed-owner",
        status="finished",
        history=history,
        budget=ExecutionBudget(),
        output="seeded",
    )
    return definition, history


async def settle(store, run_id, *, include_approval=True):
    async with asyncio.timeout(5):
        while True:
            row = store.get(run_id, "user")
            waiting = {"queued", "running"} | (
                {"waiting_approval"} if not include_approval else set()
            )
            if row["status"] not in waiting:
                return row
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("summary_mode", ["valid", "bad_reference", "error", "incomplete"])
async def test_context_projection_preserves_originals_and_counts_summary_attempts(
    store, summary_mode
):
    definition, original = seed_history(store)
    summary_calls, main_calls = [], []

    def summarize(messages, info):
        assert not info.function_tools
        material = json.loads(messages[-1].parts[-2].content)
        summary_calls.append(material)
        if summary_mode == "error":
            raise RuntimeError("summary unavailable")
        index = material[0]["message_index"] if summary_mode != "bad_reference" else 999999
        return ModelResponse(
            parts=[TextPart(f"[m{index}] 早先记录仅用于背景，未执行写入。")],
            finish_reason="length" if summary_mode == "incomplete" else "stop",
        )

    async def answer(messages, info):
        main_calls.append(messages)
        assert info.model_settings["max_tokens"] == 512
        text = str(messages)
        assert "原始目标：调查 orders" in text
        assert "最新修正：只分析 customers" in text
        assert (MEMORY_LABEL.strip() if summary_mode == "valid" else FALLBACK_LABEL) in text
        yield "按最新要求，仅分析 customers。"

    model = FunctionModel(summarize, stream_function=answer)
    factory = ModelFactory(lambda: config(context_window_tokens=4096, max_output_tokens=512))
    service = AgentRunService(
        store,
        AsyncMock(return_value=model),
        {},
        capabilities_for_run=lambda _: frozenset(),
        model_snapshot_factory=factory.snapshot,
    )
    await service.start()
    try:
        row = service.submit(
            "conversation",
            "user",
            "compact",
            "最新修正：只分析 customers，不查询数据库。",
            definition,
        )
        final = await settle(store, row["id"])
        assert final["status"] == "finished"
        assert len(summary_calls) == 1
        assert len(main_calls) == 1
        assert final["budget"]["usage"]["requests"] == 2
        history = store.history(row["id"], "user")
        assert history[: len(original)] == original
        assert len(history) == len(original) + 2
        assert MEMORY_LABEL.strip() not in str(history) and FALLBACK_LABEL not in str(history)
        events = store.read_events(row["id"], "user")
        statuses = [e for e in events if e["kind"] == "context_status"]
        assert statuses
        assert statuses[-1]["payload"]["context_window_tokens"] == 4096
        assert statuses[-1]["payload"]["used_percent"] > 0
        compact = next(e for e in events if e["kind"] == "context_compacted")
        assert compact["payload"]["mode"] == ("summary" if summary_mode == "valid" else "trimmed")
        assert compact["payload"]["after_tokens"] <= 4096
        assert (
            compact["payload"]["after_context_tokens"] < compact["payload"]["before_context_tokens"]
        )
        assert [e["kind"] for e in events].index("context_compacted") < [
            e["kind"] for e in events
        ].index("request_started")
    finally:
        await service.close()
        await factory.close()


@pytest.mark.parametrize("large_instructions", [False, True])
async def test_protected_context_limit_stops_without_discarding_user_input(
    store, large_instructions
):
    calls = []

    async def unexpected(messages, info):
        calls.append(1)
        yield "must not happen"

    factory = ModelFactory(lambda: config(context_window_tokens=2048, max_output_tokens=256))
    service = AgentRunService(
        store,
        AsyncMock(return_value=FunctionModel(stream_function=unexpected)),
        {},
        capabilities_for_run=lambda _: frozenset(),
        model_snapshot_factory=factory.snapshot,
    )
    await service.start()
    try:
        prompt = "保留我的约束" * (1 if large_instructions else 600)
        row = service.submit(
            "conversation",
            "user",
            "too-large",
            prompt,
            AgentDefinition(
                name="test",
                tool_names=frozenset(),
                instructions="平台指令" * 700 if large_instructions else "回答问题",
            ),
        )
        final = await settle(store, row["id"])
        assert final["status"] == "limited" and final["error_code"] == "context_limit"
        assert final["budget"]["usage"]["requests"] == 0
        assert calls == []
        assert store.history(row["id"], "user")[0].parts[0].content == prompt
    finally:
        await service.close()
        await factory.close()


def test_multiple_calls_stay_paired_and_unresolved_batches_are_protected():
    history = [
        ModelRequest(parts=[UserPromptPart("original")]),
        ModelResponse(parts=[ToolCallPart("a", {}, "call-a"), ToolCallPart("b", {}, "call-b")]),
        ModelRequest(parts=[ToolReturnPart("a", "done", "call-a")]),
        ModelRequest(parts=[ToolReturnPart("b", "denied", "call-b", outcome="denied")]),
    ]
    assert message_groups(history)[1].indices == (1, 2, 3)
    incomplete = message_groups(history[:-1])
    assert incomplete[1].unresolved
    assert {1, 2} <= protected_indices(history[:-1], incomplete)


def test_orphan_tool_results_are_not_silently_repaired_or_summarized():
    with pytest.raises(ValueError, match="no original call"):
        message_groups([ModelRequest(parts=[ToolReturnPart("a", "done", "unknown")])])


async def test_last_request_budget_is_reserved_for_main_answer_not_summary(store):
    from sqlalchemy import update

    from app.models.agent_runs import runs

    definition, _ = seed_history(store)
    calls = []

    def unexpected_summary(messages, info):
        raise AssertionError("No summary request budget remains")

    async def answer(messages, info):
        calls.append(messages)
        assert FALLBACK_LABEL in str(messages)
        yield "说明已知限制。"

    factory = ModelFactory(lambda: config(context_window_tokens=4096, max_output_tokens=512))
    service = AgentRunService(
        store,
        AsyncMock(return_value=FunctionModel(unexpected_summary, stream_function=answer)),
        {},
        capabilities_for_run=lambda _: frozenset(),
        model_snapshot_factory=factory.snapshot,
    )
    row = service.submit("conversation", "user", "last-request", "按原限制继续。", definition)
    budget = row["budget"] | {"request_limit": 1}
    with store.sessions.begin() as db:
        db.execute(update(runs).where(runs.c.id == row["id"]).values(budget=budget))
    await service.start()
    try:
        final = await settle(store, row["id"])
        assert final["status"] == "finished"
        assert final["budget"]["usage"]["requests"] == len(calls) == 1
        compact = next(
            e for e in store.read_events(row["id"], "user") if e["kind"] == "context_compacted"
        )
        assert compact["payload"]["reason"] == "summary_request_budget"
    finally:
        await service.close()
        await factory.close()


@pytest.mark.parametrize("refresh_fails", [False, True])
async def test_incremental_summary_survives_service_restart_without_losing_source_identity(
    store, refresh_fails
):
    definition, original = seed_history(store)
    materials = []

    def summarize(messages, info):
        material = json.loads(messages[-1].parts[-2].content)
        materials.append(material)
        if refresh_fails and len(materials) > 1:
            raise RuntimeError("refresh failed")
        references = []
        for entry in material:
            references.extend(entry.get("source_indices", [entry.get("message_index")]))
        return ModelResponse(parts=[TextPart(f"[m{references[0]}] 历史检查结果，没有执行写入。")])

    async def answer(messages, info):
        assert "原始目标：调查 orders" in str(messages)
        assert "历史检查结果，没有执行写入" in str(messages)
        message_groups(messages)  # Projection is still a valid native tool history.
        yield "仅分析，不修改。"

    factory = ModelFactory(lambda: config(context_window_tokens=4096, max_output_tokens=512))
    model = FunctionModel(summarize, stream_function=answer)
    runs = []
    try:
        for index, prompt in enumerate(
            [
                "最新修正：只分析 customers。",
                "补充材料（只供分析）:" + "new detail " * 330,
            ]
        ):
            service = AgentRunService(
                store,
                AsyncMock(return_value=model),
                {},
                capabilities_for_run=lambda _: frozenset(),
                model_snapshot_factory=factory.snapshot,
            )
            await service.start()
            try:
                row = service.submit(
                    "conversation", "user", f"incremental-{index}", prompt, definition
                )
                final = await settle(store, row["id"])
                assert final["status"] == "finished"
                runs.append(row)
            finally:
                await service.close()
        assert len(materials) == 2
        prior = materials[1][0]
        assert "prior_summary" in prior
        assert set(prior["source_indices"]).isdisjoint(
            entry["message_index"] for entry in materials[1][1:]
        )
        first_event = next(
            e for e in store.read_events(runs[0]["id"], "user") if e["kind"] == "context_compacted"
        )
        second_event = next(
            e for e in store.read_events(runs[1]["id"], "user") if e["kind"] == "context_compacted"
        )
        assert second_event["payload"]["base_snapshot_id"] == first_event["payload"]["snapshot_id"]
        assert second_event["payload"]["mode"] == (
            "partial_summary" if refresh_fails else "summary"
        )
        if refresh_fails:
            assert second_event["payload"]["snapshot_id"] == first_event["payload"]["snapshot_id"]
            assert second_event["payload"]["omitted_indices"]
            assert set(second_event["payload"]["summary_source_indices"]).isdisjoint(
                second_event["payload"]["omitted_indices"]
            )
        history = store.history(runs[-1]["id"], "user")
        assert history[: len(original)] == original
        assert len(history) == len(original) + 4
    finally:
        await factory.close()


def test_grouped_summary_references_are_checked_individually():
    assert summary_references("事实 [m2, m3] 和 [m4]") == {2, 3, 4}
    assert valid_summary("事实 [m2, m3]", [2, 3], 100)
    assert not valid_summary("事实 [m2, m999]", [2, 3], 100)
    assert not valid_summary("事实 [m2-m9]", range(10), 100)


async def test_long_single_run_compacts_paired_tool_batches_and_resumes_original_approval(store):
    from pydantic_ai import Tool
    from pydantic_ai.models.function import DeltaToolCall
    from test_service import allow_read, approve_write

    from app.services.agent.execution import RegisteredTool

    writes, materials, wire_histories = [], [], []
    calls = 0

    async def read() -> str:
        return "observed bounded detail " * 60

    async def write() -> str:
        writes.append(1)
        return "written"

    def summarize(messages, info):
        material = json.loads(messages[-1].parts[-2].content)
        materials.append(material)
        index = material[0].get("message_index", 1)
        return ModelResponse(parts=[TextPart(f"[m{index}] 已读取样本，只是观测结果。")])

    async def answer(messages, info):
        nonlocal calls
        calls += 1
        wire_histories.append(messages)
        message_groups(messages)
        assert "原始目标：读取样本，写入需批准" in str(messages)
        if calls <= 9:
            yield {0: DeltaToolCall(name="read", json_args="{}", tool_call_id=f"read-{calls}")}
        elif calls == 10:
            yield {0: DeltaToolCall(name="write", json_args="{}", tool_call_id="approved-original")}
        else:
            assert any(
                isinstance(part, ToolReturnPart) and part.tool_call_id == "approved-original"
                for message in messages
                for part in message.parts
            )
            yield "批准的写入已执行。"

    factory = ModelFactory(lambda: config(context_window_tokens=4096, max_output_tokens=512))
    service = AgentRunService(
        store,
        AsyncMock(return_value=FunctionModel(summarize, stream_function=answer)),
        {
            "read": RegisteredTool(tool=Tool(read), authorize=allow_read),
            "write": RegisteredTool(
                tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
            ),
        },
        capabilities_for_run=lambda _: frozenset({"read", "write"}),
        model_snapshot_factory=factory.snapshot,
        poll_seconds=0.01,
    )
    await service.start()
    try:
        row = service.submit(
            "conversation",
            "user",
            "long-run",
            "原始目标：读取样本，写入需批准。",
            AgentDefinition(name="long", tool_names=frozenset({"read", "write"})),
        )
        paused = await settle(store, row["id"])
        assert paused["status"] == "waiting_approval"
        assert writes == []
        pending = next(
            call for call in paused["tool_calls"] if call["call_id"] == "approved-original"
        )
        service.approve(row["id"], "user", "approved-original", pending["fingerprint"], True)
        final = await settle(store, row["id"], include_approval=False)
        assert final["status"] == "finished"
        assert writes == [1]
        assert materials
        assert final["budget"]["usage"]["requests"] == calls + len(materials)
        full = store.history(row["id"], "user")
        message_groups(full)
        ids = [
            part.tool_call_id
            for message in full
            for part in message.parts
            if isinstance(part, ToolCallPart)
        ]
        assert ids == [*(f"read-{i}" for i in range(1, 10)), "approved-original"]
        assert len(wire_histories[-1]) < len(full)
        assert MEMORY_LABEL.strip() not in str(full)
    finally:
        await service.close()
        await factory.close()
