import asyncio
import json
import threading

import httpx
import pytest
from fastapi import FastAPI
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from test_models import config

from app.api.schedules import router
from app.db.base import Base
from app.models.models import Agent, Schedule
from app.services.agent.application import RuntimeApplication
from app.services.agent.models import ModelFactory
from app.services.scheduler.builder import SchedulerBuilderService


def proposal(patch):
    return json.dumps(
        {"patch": patch, "summary": "建议按新的时间运行，尚未执行。"}, ensure_ascii=False
    )


def proposed(patch):
    return ModelResponse([ToolCallPart("return_result", proposal(patch))])


@pytest.fixture
def application(store):
    Base.metadata.create_all(store.sessions.kw["bind"])
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    with store.sessions.begin() as db:
        agent = Agent(name="test", prompt="Answer", tools=[], status="active")
        db.add(agent)
        db.flush()
        schedule = Schedule(
            name="test",
            target_type="agent",
            target_id=agent.id,
            input_prompt="Explain the result",
            schedule_type="interval",
            interval_seconds=300,
            status="paused",
            max_retries=0,
            timezone="UTC",
        )
        db.add(schedule)
        db.flush()
        schedule_id, agent_id = schedule.id, agent.id
    app = FastAPI()
    app.state.agent_runtime = runtime
    app.include_router(router, prefix="/api/v1")
    return app, runtime, schedule_id, agent_id


async def test_native_factory_resolves_once_off_loop_and_uses_pinned_settings(monkeypatch):
    loop_thread = threading.get_ident()
    loaders, requests = [], []

    def load():
        loaders.append(threading.get_ident())
        return config(max_output_tokens=1234, temperature=0.3)

    def answer(messages, info):
        requests.append(messages)
        assert info.function_tools == [] and info.output_tools == []
        assert info.model_settings == {"max_tokens": 1234, "temperature": 0.3}
        return ModelResponse([TextPart("native answer")])

    factory = ModelFactory(load)
    monkeypatch.setattr(factory, "_build", lambda _: FunctionModel(answer))
    try:
        assert (
            await factory.text(instructions="answer", prompt="input", purpose="test")
            == "native answer"
        )
        assert len(loaders) == len(requests) == 1
        assert loaders[0] != loop_thread
    finally:
        await factory.close()


async def test_one_shot_total_deadline_cancels_the_request_without_an_outer_retry(monkeypatch):
    attempts, cancellations = [], []

    async def answer(messages, info):
        attempts.append(1)
        try:
            await asyncio.Event().wait()
        finally:
            cancellations.append(1)

    factory = ModelFactory(lambda: config(timeout_seconds=0.05))
    monkeypatch.setattr(factory, "_build", lambda _: FunctionModel(answer))
    try:
        with pytest.raises(TimeoutError):
            await factory.text(instructions="answer", prompt="input", purpose="test_timeout")
        assert attempts == cancellations == [1]
    finally:
        await factory.close()


async def test_structured_one_shot_uses_one_output_tool_without_business_dispatch(monkeypatch):
    import httpx2
    from test_provider import use_mock_transport

    requests = []

    def answer(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert "response_format" not in payload
        assert payload["tool_choice"] == "required"
        assert len(payload["tools"]) == 1
        assert payload["tools"][0]["function"]["name"] == "return_result"
        assert "interval_seconds" in str(payload["tools"])
        return httpx2.Response(
            200,
            json={
                "id": "json-response",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "output-1",
                                    "type": "function",
                                    "function": {
                                        "name": "return_result",
                                        "arguments": proposal({"interval_seconds": 900}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            },
        )

    use_mock_transport(monkeypatch, answer)
    factory = ModelFactory(lambda: config())
    try:
        result = await SchedulerBuilderService(factory).apply_prompt(
            "每十五分钟", {"target_type": "agent", "schedule_type": "interval"}
        )
        assert result.patch == {"interval_seconds": 900}
        assert len(requests) == 1
    finally:
        await factory.close()


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"patch":{"target_id":9},"summary":"bad"}',
        '{"patch":{"interval_seconds":"300"},"summary":"bad"}',
        '{"patch":{"status":null},"summary":"bad"}',
    ],
)
async def test_invalid_proposal_is_not_repaired_or_defaulted(text, monkeypatch):
    calls = []
    factory = ModelFactory(lambda: config())

    def answer(messages, info):
        calls.append(messages)
        return ModelResponse([ToolCallPart("return_result", text)])

    monkeypatch.setattr(factory, "_build", lambda _: FunctionModel(answer))
    try:
        with pytest.raises(ValueError):
            await SchedulerBuilderService(factory).apply_prompt(
                "调整时间", {"target_type": "agent"}
            )
        assert len(calls) == 1
    finally:
        await factory.close()


@pytest.mark.parametrize(
    "response",
    [
        ModelResponse([TextPart(proposal({"interval_seconds": 900}))]),
        ModelResponse([ToolCallPart("wrong_name", proposal({}))]),
        ModelResponse(
            [
                ToolCallPart("return_result", proposal({})),
                ToolCallPart("return_result", proposal({})),
            ]
        ),
        ModelResponse([ToolCallPart("return_result", proposal({}))], finish_reason="length"),
    ],
)
async def test_structured_request_rejects_missing_ambiguous_or_incomplete_output(
    response, monkeypatch
):
    calls = []

    def answer(messages, info):
        calls.append(messages)
        assert info.function_tools == []
        assert [tool.name for tool in info.output_tools] == ["return_result"]
        return response

    factory = ModelFactory(lambda: config())
    monkeypatch.setattr(factory, "_build", lambda _: FunctionModel(answer))
    try:
        with pytest.raises(ValueError):
            await SchedulerBuilderService(factory).apply_prompt(
                "调整时间", {"target_type": "agent"}
            )
        assert len(calls) == 1
    finally:
        await factory.close()


async def test_config_api_updates_with_one_native_request(application, monkeypatch):
    app, runtime, schedule_id, _ = application
    seen = []

    def answer(messages, info):
        payload = json.loads(messages[-1].parts[-1].content)
        seen.append(payload)
        assert payload["current"]["target_type"] == "agent"
        assert "input_prompt" not in payload["current"]
        return proposed({"interval_seconds": 600})

    monkeypatch.setattr(runtime.models, "_build", lambda _: FunctionModel(answer))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            f"/api/v1/schedules/{schedule_id}/build", json={"prompt": "改为每十分钟，保持暂停"}
        )
    assert result.status_code == 200, result.text
    assert result.json()["schedule"]["interval_seconds"] == 600
    assert result.json()["schedule"]["status"] == "paused"
    assert len(seen) == 1
    await runtime.close()


async def test_config_change_during_model_wait_returns_conflict_without_overwriting(
    application, monkeypatch
):
    app, runtime, schedule_id, _ = application
    entered, release = asyncio.Event(), asyncio.Event()

    async def answer(messages, info):
        assert runtime.sessions.kw["bind"].pool.checkedout() == 0
        entered.set()
        await release.wait()
        return proposed({"interval_seconds": 600})

    monkeypatch.setattr(runtime.models, "_build", lambda _: FunctionModel(answer))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        pending = asyncio.create_task(
            client.post(f"/api/v1/schedules/{schedule_id}/build", json={"prompt": "改为十分钟"})
        )
        await asyncio.wait_for(entered.wait(), 2)
        # No connection is held during the network wait; another editor can save.
        with runtime.sessions.begin() as db:
            schedule = db.get(Schedule, schedule_id)
            schedule.interval_seconds = 900
        release.set()
        result = await pending
    assert result.status_code == 409, result.text
    with runtime.sessions() as db:
        assert db.get(Schedule, schedule_id).interval_seconds == 900
    await runtime.close()


@pytest.mark.parametrize(
    "patch", [{"max_retries": 3}, {"interval_seconds": None}, {"timezone": "not/a/zone"}]
)
async def test_domain_validation_rolls_back_proposal_and_claim(application, monkeypatch, patch):
    app, runtime, schedule_id, _ = application
    with runtime.sessions() as db:
        original = db.get(Schedule, schedule_id).updated_at
    monkeypatch.setattr(
        runtime.models,
        "_build",
        lambda _: FunctionModel(lambda _m, _i: proposed(patch)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            f"/api/v1/schedules/{schedule_id}/build", json={"prompt": "修改配置"}
        )
    assert result.status_code in {400, 422}, result.text
    with runtime.sessions() as db:
        schedule = db.get(Schedule, schedule_id)
        assert schedule.interval_seconds == 300 and schedule.max_retries == 0
        assert schedule.updated_at == original
    await runtime.close()


async def test_ai_create_uses_same_domain_validation_and_explicit_target(application, monkeypatch):
    app, runtime, _, agent_id = application
    monkeypatch.setattr(
        runtime.models,
        "_build",
        lambda _: FunctionModel(
            lambda _m, _i: proposed(
                {
                    "schedule_type": "interval",
                    "interval_seconds": 900,
                    "cron_expression": None,
                }
            )
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/api/v1/schedules/ai-create",
            json={
                "prompt": "每15分钟",
                "target_type": "agent",
                "target_id": agent_id,
                "input_prompt": "解释结果",
                "status": "paused",
            },
        )
    assert result.status_code == 201, result.text
    saved = result.json()["schedule"]
    assert saved["target_id"] == agent_id and saved["interval_seconds"] == 900
    assert saved["status"] == "paused" and saved["next_run_at"] is None
    await runtime.close()
