# semantic-guard: allow — message here is an LLM API message dict, not user input
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger

logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("litellm").setLevel(logging.WARNING)

logger = get_logger("chat.llm")
tracer = trace.get_tracer("app.services.llm")


_MAX_TRACE_TEXT = 800
_MAX_TRACE_MESSAGES = 12
_MAX_TRACE_TOOLS = 8


def _clip_text(value: Any, limit: int = _MAX_TRACE_TEXT) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[:limit]}...<truncated>"


def _summarize_message(message: dict[str, Any]) -> dict[str, Any]:
    role = str(message.get("role") or "")
    item: dict[str, Any] = {"role": role}
    if "name" in message:
        item["name"] = message.get("name")
    if "tool_call_id" in message:
        item["tool_call_id"] = message.get("tool_call_id")

    content = message.get("content")
    if isinstance(content, str) and content:
        item["content_preview"] = _clip_text(content)
    elif content is not None:
        item["content_preview"] = _clip_text(json.dumps(content, ensure_ascii=False, default=str))

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        item["tool_calls"] = []
        for tc in tool_calls[:_MAX_TRACE_TOOLS]:
            if not isinstance(tc, dict):
                item["tool_calls"].append({"preview": _clip_text(tc)})
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            item["tool_calls"].append(
                {
                    "id": tc.get("id"),
                    "type": tc.get("type"),
                    "function": {
                        "name": fn.get("name"),
                        "arguments_preview": _clip_text(fn.get("arguments")),
                    },
                }
            )
        if len(tool_calls) > _MAX_TRACE_TOOLS:
            item["tool_calls_truncated"] = len(tool_calls) - _MAX_TRACE_TOOLS
    return item


def _summarize_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not tools:
        return []
    summarized: list[dict[str, Any]] = []
    for tool in tools[:_MAX_TRACE_TOOLS]:
        if not isinstance(tool, dict):
            summarized.append({"preview": _clip_text(tool)})
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        summarized.append(
            {
                "type": tool.get("type"),
                "function": {
                    "name": function.get("name"),
                    "description_preview": _clip_text(function.get("description"), 200),
                },
            }
        )
    return summarized


def _log_outbound_payload(
    *,
    model: str,
    base_url: str,
    stream: bool,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    reasoning_config: dict[str, Any] | None,
) -> None:
    payload = {
        "base_url": base_url,
        "model": model,
        "stream": stream,
        "message_count": len(messages),
        "tool_count": len(tools or []),
        "reasoning_config": reasoning_config,
        "messages": [_summarize_message(message) for message in messages[-_MAX_TRACE_MESSAGES:]],
        "messages_truncated": max(0, len(messages) - _MAX_TRACE_MESSAGES),
        "tools": _summarize_tools(tools),
        "tools_truncated": max(0, len(tools or []) - _MAX_TRACE_TOOLS),
    }
    logger.info("llm_outbound_payload %s", json.dumps(payload, ensure_ascii=False, default=str))


def _resolve_llm_config() -> dict[str, str]:
    """Read LLM config from platform_settings, falling back to env vars."""
    s = get_settings()
    result = {
        "base_url": s.ai_base_url,
        "api_key": s.ai_api_key,
        "model": s.ai_model,
    }
    try:
        from app.db.database import SessionLocal
        from app.models.models import PlatformSetting

        with SessionLocal() as db:
            rows = (
                db.query(PlatformSetting)
                .filter(PlatformSetting.key.in_(["ai_base_url", "ai_api_key", "ai_model"]))
                .all()
            )
            for row in rows:
                if row.value:
                    key = row.key
                    field = key[3:]  # strip "ai_" → "api_key" / "base_url" / "model"
                    result[field] = str(row.value)
    except Exception:
        pass
    return result


class RateLimitError(Exception):
    def __init__(self, retries: int, wait_seconds: int):
        self.retries = retries
        self.wait_seconds = wait_seconds
        super().__init__(
            f"Model service rate limited (HTTP 429); retried {retries} time(s) at {wait_seconds}s intervals but still failed."
        )


class LLMClient:
    def __init__(self):
        cfg = _resolve_llm_config()
        self.base_url = cfg["base_url"]
        self.api_key = cfg["api_key"]
        self.model = cfg["model"]

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = True,
        *,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_config: dict[str, Any] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        import litellm

        # LiteLLM requires a provider prefix (e.g. "openai/qwen3-max") when
        # routing to a custom OpenAI-compatible base_url.
        model = self.model
        if self.base_url and "/" not in model:
            model = f"openai/{model}"

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "api_base": self.base_url,
            "api_key": self.api_key,
        }
        if stream:
            # OpenAI-compatible providers emit token usage in a final empty-choice
            # chunk when explicitly requested.  The reasoning engine consumes it
            # for trajectory cost metrics without exposing it to the model.
            kwargs["stream_options"] = {"include_usage": True}
        if tools:
            kwargs["tools"] = tools
            kwargs["parallel_tool_calls"] = bool(
                get_settings().agent_parallel_read_only_enabled
            )
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if temperature is not None:
            kwargs["temperature"] = temperature
        if response_format is not None:
            kwargs["response_format"] = response_format
        if reasoning_config is not None:
            kwargs["thinking"] = reasoning_config

        logger.info(
            "llm_call_start %s",
            fmt_kv(base_url=self.base_url, model=model, stream=stream),
        )
        _log_outbound_payload(
            model=model,
            base_url=self.base_url,
            stream=stream,
            messages=messages,
            tools=tools,
            reasoning_config=reasoning_config,
        )

        _span = tracer.start_span(
            "llm.chat",
            kind=trace.SpanKind.CLIENT,
            attributes={
                "llm.model": model,
                "llm.stream": stream,
                "llm.message_count": len(messages),
                "llm.has_tools": bool(tools),
            },
        )
        _ctx = trace.set_span_in_context(_span)
        _token = otel_context.attach(_ctx)

        try:
            if stream:
                response = await litellm.acompletion(**kwargs)
                async for chunk in response:
                    yield chunk.model_dump()
            else:
                response = await litellm.acompletion(**kwargs)
                yield response.model_dump()

            _span.set_status(trace.StatusCode.OK)
        except litellm.RateLimitError as e:
            logger.warning("llm_rate_limited error=%s", str(e))
            _span.record_exception(e)
            _span.set_status(trace.StatusCode.ERROR, str(e))
            raise RateLimitError(retries=0, wait_seconds=0) from e
        except litellm.BadRequestError as e:
            logger.error("llm_bad_request error=%s", str(e))
            _span.record_exception(e)
            _span.set_status(trace.StatusCode.ERROR, str(e))
            raise
        except Exception as e:
            logger.exception("llm_call_error error=%s", str(e))
            _span.record_exception(e)
            _span.set_status(trace.StatusCode.ERROR, str(e))
            raise
        finally:
            otel_context.detach(_token)
            _span.end()


_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
