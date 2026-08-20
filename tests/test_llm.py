from __future__ import annotations

import asyncio
import contextvars
import logging
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app.services.llm as llm_module
from app.services.llm import LLMClient


class _ModelPayload:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return self._payload


class _AsyncResponse:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = iter(payloads)
        self.observed_spans: list[trace.Span] = []

    def __aiter__(self) -> _AsyncResponse:
        return self

    async def __anext__(self) -> _ModelPayload:
        self.observed_spans.append(trace.get_current_span())
        try:
            payload = next(self._payloads)
        except StopIteration:
            raise StopAsyncIteration from None
        return _ModelPayload(payload)


def _client() -> LLMClient:
    client = object.__new__(LLMClient)
    client.base_url = ""
    client.api_key = "test-key"
    client.model = "test-model"
    return client


def _install_tracer(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(llm_module, "tracer", provider.get_tracer("tests.llm"))
    return exporter


def _install_completion(monkeypatch: pytest.MonkeyPatch, completion: Any) -> SimpleNamespace:
    unsupported_params_error = type("UnsupportedParamsError", (Exception,), {})
    module = SimpleNamespace(
        acompletion=completion,
        RateLimitError=type("RateLimitError", (Exception,), {}),
        BadRequestError=type("BadRequestError", (Exception,), {}),
        UnsupportedParamsError=unsupported_params_error,
    )
    monkeypatch.setitem(
        sys.modules,
        "litellm",
        module,
    )
    return module


@pytest.mark.asyncio
async def test_structured_output_retries_without_unsupported_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    provider: SimpleNamespace

    async def fake_completion(**kwargs: Any) -> _ModelPayload:
        calls.append(kwargs)
        if len(calls) == 1:
            raise provider.UnsupportedParamsError("unsupported response format")
        return _ModelPayload({"choices": [{"message": {"content": '{"ok":true}'}}]})

    provider = _install_completion(monkeypatch, fake_completion)
    generator = _client().chat(
        messages=[{"role": "user", "content": "return json"}],
        stream=False,
        response_format={"type": "json_object"},
    )

    payload = await anext(generator)

    assert payload["choices"][0]["message"]["content"] == '{"ok":true}'
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1]


@pytest.mark.asyncio
async def test_non_streaming_result_restores_context_before_yield(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    exporter = _install_tracer(monkeypatch)
    parent_span = trace.get_current_span()
    observed_spans: list[trace.Span] = []

    async def fake_completion(**kwargs: Any) -> _ModelPayload:
        assert kwargs["stream"] is False
        observed_spans.append(trace.get_current_span())
        return _ModelPayload({"choices": [{"message": {"content": "ok"}}]})

    _install_completion(monkeypatch, fake_completion)
    generator = _client().chat(messages=[{"role": "user", "content": "hello"}], stream=False)

    payload = await anext(generator)

    assert payload["choices"][0]["message"]["content"] == "ok"
    assert observed_spans[0] is not parent_span
    assert trace.get_current_span() is parent_span
    assert len(exporter.get_finished_spans()) == 1

    caplog.set_level(logging.ERROR, logger="opentelemetry.context")
    close_task = contextvars.Context().run(asyncio.create_task, generator.aclose())
    await close_task
    assert "Failed to detach context" not in caplog.text


@pytest.mark.asyncio
async def test_streaming_result_restores_context_between_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = _install_tracer(monkeypatch)
    parent_span = trace.get_current_span()
    response = _AsyncResponse(
        [
            {"choices": [{"delta": {"content": "a"}}]},
            {"choices": [{"delta": {"content": "b"}}]},
        ]
    )

    async def fake_completion(**kwargs: Any) -> _AsyncResponse:
        assert kwargs["stream"] is True
        assert trace.get_current_span() is not parent_span
        return response

    _install_completion(monkeypatch, fake_completion)
    observed_consumer_spans: list[trace.Span] = []
    chunks: list[dict[str, Any]] = []

    async for chunk in _client().chat(
        messages=[{"role": "user", "content": "hello"}],
        stream=True,
    ):
        chunks.append(chunk)
        observed_consumer_spans.append(trace.get_current_span())

    assert [item["choices"][0]["delta"]["content"] for item in chunks] == ["a", "b"]
    assert all(span is not parent_span for span in response.observed_spans)
    assert observed_consumer_spans == [parent_span, parent_span]
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code is trace.StatusCode.OK


@pytest.mark.asyncio
async def test_streaming_result_can_close_from_a_different_context(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    exporter = _install_tracer(monkeypatch)
    parent_span = trace.get_current_span()
    response = _AsyncResponse([{"choices": [{"delta": {"content": "a"}}]}])

    async def fake_completion(**kwargs: Any) -> _AsyncResponse:
        return response

    _install_completion(monkeypatch, fake_completion)
    generator = _client().chat(messages=[{"role": "user", "content": "hello"}], stream=True)

    await anext(generator)

    assert trace.get_current_span() is parent_span
    assert exporter.get_finished_spans() == ()
    caplog.set_level(logging.ERROR, logger="opentelemetry.context")
    close_task = contextvars.Context().run(asyncio.create_task, generator.aclose())
    await close_task
    assert len(exporter.get_finished_spans()) == 1
    assert "Failed to detach context" not in caplog.text


@pytest.mark.asyncio
async def test_cancelled_stream_ends_span_and_restores_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = _install_tracer(monkeypatch)
    parent_span = trace.get_current_span()
    iteration_started = asyncio.Event()

    class _BlockingResponse:
        def __aiter__(self) -> _BlockingResponse:
            return self

        async def __anext__(self) -> _ModelPayload:
            iteration_started.set()
            await asyncio.Event().wait()
            raise StopAsyncIteration

    async def fake_completion(**kwargs: Any) -> _BlockingResponse:
        return _BlockingResponse()

    _install_completion(monkeypatch, fake_completion)
    generator = _client().chat(messages=[{"role": "user", "content": "hello"}], stream=True)
    next_chunk = asyncio.create_task(anext(generator))
    await iteration_started.wait()

    next_chunk.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_chunk

    assert trace.get_current_span() is parent_span
    assert len(exporter.get_finished_spans()) == 1


@pytest.mark.asyncio
async def test_completion_error_ends_failed_span_and_restores_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = _install_tracer(monkeypatch)
    parent_span = trace.get_current_span()

    async def fake_completion(**kwargs: Any) -> _ModelPayload:
        assert trace.get_current_span() is not parent_span
        raise RuntimeError("provider unavailable")

    _install_completion(monkeypatch, fake_completion)
    generator = _client().chat(messages=[{"role": "user", "content": "hello"}], stream=False)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await anext(generator)

    assert trace.get_current_span() is parent_span
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code is trace.StatusCode.ERROR
