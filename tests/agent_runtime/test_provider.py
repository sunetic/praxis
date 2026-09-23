"""Exercise the real SDK's HTTP and SSE conversion with an in-memory transport."""

import json

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic_ai import Tool, models
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from app.services.agent import models as model_factory
from app.services.agent.definitions import AgentDefinition, RunDependencies
from app.services.agent.models import ModelConnectionConfig, open_model
from app.services.agent.runtime import ExecutionBudget, run_agent


def use_mock_transport(monkeypatch, handler):
    def client(**kwargs):
        return AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(handler), trust_env=False
            ),
        )

    monkeypatch.setattr(model_factory, "AsyncOpenAI", client)
    # All I/O goes through the mock transport above; no external endpoint is used.
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)


def stream_response(*deltas, finish_reason="stop", response_id="response-1"):
    chunks = []
    for delta in deltas:
        chunks.append(
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
        )
    chunks.append(
        {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "fixture-model",
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        }
    )
    chunks.append(
        {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "fixture-model",
            "choices": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        }
    )
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, content=body.encode()
    )


async def execute(**kwargs):
    configuration = ModelConnectionConfig(
        model_name="fixture-model",
        base_url="https://fixture.invalid/v1",
        api_key="fixture-key",
        transport_retries=0,
    )
    async with open_model(configuration) as model:
        return await run_agent(
            definition=AgentDefinition(
                name="provider-test", tool_names=frozenset(kwargs.get("tools", {}))
            ),
            model=model,
            deps=RunDependencies(run_id="run", conversation_id="conversation", actor_id="user"),
            authorized_tool_names=frozenset(kwargs.get("tools", {})),
            budget=kwargs.pop("budget", ExecutionBudget()),
            prompt="Read the fixture.",
            **kwargs,
        )


async def test_native_provider_streams_text_and_tools_and_preserves_usage(monkeypatch):
    requests = []
    observed = []

    async def read(key: str) -> str:
        observed.append(key)
        return "42"

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert str(request.url) == "https://fixture.invalid/v1/chat/completions"
        assert payload["stream"] is True
        assert payload["stream_options"]["include_usage"] is True
        if len(requests) == 1:
            return stream_response(
                {"role": "assistant", "content": "查询中。"},
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "native-call",
                            "type": "function",
                            "function": {"name": "read", "arguments": '{"key":'},
                        }
                    ]
                },
                {"tool_calls": [{"index": 0, "function": {"arguments": '"count"}'}}]},
                finish_reason="tool_calls",
            )
        assert len(requests) == 2
        return stream_response(
            {"content": "结果是 "}, {"content": "42。"}, response_id="response-2"
        )

    use_mock_transport(monkeypatch, handler)
    budget = ExecutionBudget()
    events = []

    async def record(_ctx, stream):
        async for event in stream:
            events.append(event)

    result = await execute(tools={"read": Tool(read)}, budget=budget, event_stream_handler=record)
    assert result.output == "结果是 42。"
    assert observed == ["count"]
    assert budget.usage.requests == 2
    assert budget.usage.input_tokens == 20
    assert budget.usage.output_tokens == 8
    tool_messages = [message for message in requests[1]["messages"] if message["role"] == "tool"]
    assert tool_messages[0]["tool_call_id"] == "native-call"
    assert len(events) > 2
    assert result.all_messages()[-1].finish_reason == "stop"
    assert result.all_messages()[-1].provider_response_id == "response-2"
    assert any(
        isinstance(part, ToolReturnPart) and part.tool_call_id == "native-call"
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


async def test_unsupported_parameter_error_is_visible_without_silent_request_rewrite(monkeypatch):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(
            400,
            json={
                "error": {
                    "message": "Unsupported parameter: temperature",
                    "type": "invalid_request_error",
                    "param": "temperature",
                    "code": "unsupported_parameter",
                }
            },
        )

    use_mock_transport(monkeypatch, handler)
    with pytest.raises(ModelHTTPError) as error:
        await execute(tools={}, model_settings={"temperature": 0.2})
    assert error.value.status_code == 400
    assert len(requests) == 1
    assert requests[0]["temperature"] == 0.2
