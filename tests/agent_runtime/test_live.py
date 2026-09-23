"""Opt-in endpoint smoke tests using synthetic data and no external business tools."""

import os
import secrets
from time import monotonic

import pytest
from pydantic_ai import DeferredToolRequests, DeferredToolResults, Tool, models
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)

from app.services.agent.definitions import AgentDefinition, RunDependencies
from app.services.agent.models import ModelConnectionConfig, open_model
from app.services.agent.runtime import ExecutionBudget, run_agent

pytestmark = pytest.mark.llm


@pytest.fixture
def live_config(monkeypatch):
    if os.getenv("PRAXIS_RUNTIME_LIVE") != "1":
        pytest.skip("Set PRAXIS_RUNTIME_LIVE=1 to opt into synthetic endpoint tests")
    values = {
        name: os.getenv(f"PRAXIS_RUNTIME_LIVE_{name}", "")
        for name in ("MODEL", "BASE_URL", "API_KEY")
    }
    if not all(values.values()):
        pytest.fail("Explicit live MODEL, BASE_URL and API_KEY are required", pytrace=False)
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    return ModelConnectionConfig(
        model_name=values["MODEL"],
        base_url=values["BASE_URL"],
        api_key=values["API_KEY"],
        timeout_seconds=45,
        transport_retries=0,
    )


async def test_live_natural_stream(live_config, record_property):
    text = []
    first_text = None
    start = monotonic()

    async def record(_ctx, events):
        nonlocal first_text
        async for event in events:
            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                text.append(event.part.content)
            elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                text.append(event.delta.content_delta)
            if first_text is None and any(text):
                first_text = monotonic() - start

    budget = ExecutionBudget(request_limit=2, active_seconds_limit=60)
    async with open_model(live_config) as model:
        result = await run_agent(
            definition=AgentDefinition(name="live-read-only", tool_names=frozenset()),
            model=model,
            deps=RunDependencies(run_id="smoke-text", conversation_id="smoke", actor_id="test"),
            tools={},
            authorized_tool_names=frozenset(),
            budget=budget,
            prompt="用一句中文解释数据库索引的作用。",
            event_stream_handler=record,
        )
    assert isinstance(result.output, str) and result.output.strip()
    assert "".join(text) == result.output
    assert budget.usage.requests == 1
    assert first_text is not None
    record_property("first_text_seconds", round(first_text, 3))
    record_property("active_seconds", round(budget.active_seconds, 3))
    record_property("model", live_config.model_name)


async def test_live_tool_approval_round_trip(live_config, record_property):
    # This is an in-memory fixture, NOT a database/SQL/HTTP/write integration.
    marker = secrets.token_hex(8)
    reads, saved = [], []

    async def read_sample() -> str:
        """Read the current synthetic sample's opaque value."""
        reads.append(1)
        return marker

    async def save_sample(value: str) -> str:
        """Save the exact opaque value to the in-memory test fixture after approval."""
        assert value == marker
        saved.append(value)
        return "The test fixture was updated."

    tools = {
        "read_sample": Tool(read_sample),
        "save_sample": Tool(save_sample, requires_approval=True),
    }
    definition = AgentDefinition(name="live-fixture", tool_names=frozenset(tools))
    deps = RunDependencies(run_id="smoke-tools", conversation_id="smoke-tools", actor_id="test")
    budget = ExecutionBudget(request_limit=6, active_seconds_limit=120)
    async with open_model(live_config) as model:
        paused = await run_agent(
            definition=definition,
            model=model,
            deps=deps,
            tools=tools,
            authorized_tool_names=frozenset(tools),
            budget=budget,
            prompt="读取当前测试样本的值，并把原值保存到内存测试样本。保存需要批准时请等待，不要声称已经保存。",
        )
        assert isinstance(paused.output, DeferredToolRequests)
        assert len(paused.output.approvals) == 1
        assert reads and saved == []
        call_id = paused.output.approvals[0].tool_call_id
        history = ModelMessagesTypeAdapter.validate_json(paused.all_messages_json())
        resumed = await run_agent(
            definition=definition,
            model=model,
            deps=deps,
            tools=tools,
            authorized_tool_names=frozenset(tools),
            budget=budget,
            message_history=history,
            deferred_tool_results=DeferredToolResults(approvals={call_id: True}),
        )
    assert isinstance(resumed.output, str) and resumed.output.strip()
    assert saved == [marker]
    assert budget.usage.requests >= 2
    record_property("model_requests", budget.usage.requests)
    record_property("active_seconds", round(budget.active_seconds, 3))
    record_property("input_tokens", budget.usage.input_tokens)
    record_property("output_tokens", budget.usage.output_tokens)
