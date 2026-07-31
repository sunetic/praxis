from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.services.json_schema_validator import JsonSchemaValidationError, validate_json_object
from app.services.llm import LLMClient, get_llm_client

logger = get_logger("builder.runtime")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class SchedulerBuildResult:
    patch: dict[str, Any]
    summary: str


class SchedulerBuilderService:
    _PATCH_SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "schedule_type": {"type": "string", "enum": ["cron", "interval"]},
            "cron_expression": {"type": ["string", "null"], "minLength": 1},
            "interval_seconds": {"type": ["integer", "null"], "minimum": 1},
            "max_retries": {"type": "integer", "minimum": 0},
            "status": {"type": "string", "enum": ["active", "paused"]},
            "timezone": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm = llm_client or get_llm_client()

    def apply_prompt(self, prompt: str, current: dict[str, Any]) -> SchedulerBuildResult:
        normalized_prompt = _compact_whitespace(prompt)
        if not normalized_prompt:
            raise ValueError("prompt must not be empty")
        projected_current = self._project_current(current)
        parsed = self._generate_scheduler_patch_payload(
            prompt=normalized_prompt,
            current=projected_current,
        )
        raw_patch = parsed.get("patch") if isinstance(parsed.get("patch"), dict) else parsed
        patch = self._normalize_patch(
            raw_patch if isinstance(raw_patch, dict) else {}, current=projected_current
        )
        summary = str(parsed.get("summary") or "").strip() if isinstance(parsed, dict) else ""
        if not summary:
            summary = self._summarize(patch, projected_current)
        return SchedulerBuildResult(patch=patch, summary=summary)

    def _project_current(self, current: dict[str, Any]) -> dict[str, Any]:
        return {
            "schedule_type": str(current.get("schedule_type") or "cron").strip().lower() or "cron",
            "cron_expression": str(current.get("cron_expression") or "").strip() or None,
            "interval_seconds": current.get("interval_seconds"),
            "max_retries": int(current.get("max_retries") or 0),
            "status": str(current.get("status") or "active").strip().lower() or "active",
            "timezone": str(current.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai",
        }

    def _generate_scheduler_patch_payload(
        self, *, prompt: str, current: dict[str, Any]
    ) -> dict[str, Any]:
        payload = {
            "user_prompt": prompt,
            "current_schedule": current,
            "schema": {
                "patch": {
                    "schedule_type": "cron|interval",
                    "cron_expression": "string|null",
                    "interval_seconds": "integer|null",
                    "max_retries": "integer",
                    "status": "active|paused",
                    "timezone": "IANA timezone string",
                },
                "summary": "string",
            },
            "constraints": [
                "Return JSON only.",
                "Use patch fields only from: schedule_type, cron_expression, interval_seconds, max_retries, status, timezone.",
                "When schedule_type=interval, interval_seconds must be a positive integer and cron_expression should be null.",
                "When schedule_type=cron, cron_expression must be non-empty and interval_seconds should be null.",
                "status must be active or paused.",
                "Do not use keyword matching rules; infer intent semantically from full prompt.",
            ],
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a scheduler intent parser. Convert natural language into a structured scheduler patch. "
                    "Infer semantics from context and return strict JSON."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        raw = self._run_async_safely(self._call_llm_for_json(messages=messages))
        return self._parse_json_patch(raw)

    def _normalize_patch(self, patch: dict[str, Any], *, current: dict[str, Any]) -> dict[str, Any]:
        pre_normalized = dict(patch)
        if "schedule_type" in pre_normalized:
            pre_normalized["schedule_type"] = (
                str(pre_normalized.get("schedule_type") or "").strip().lower()
            )
        if "status" in pre_normalized:
            pre_normalized["status"] = str(pre_normalized.get("status") or "").strip().lower()
        if "cron_expression" in pre_normalized:
            raw_cron = pre_normalized.get("cron_expression")
            pre_normalized["cron_expression"] = (
                None if raw_cron is None else str(raw_cron).strip() or None
            )
        if "timezone" in pre_normalized:
            raw_timezone = str(pre_normalized.get("timezone") or "").strip()
            pre_normalized["timezone"] = raw_timezone or None
            if pre_normalized["timezone"] is None:
                pre_normalized.pop("timezone", None)

        try:
            normalized = validate_json_object(
                schema=self._PATCH_SCHEMA,
                payload=pre_normalized,
            )
        except JsonSchemaValidationError as err:
            raise ValueError(str(err)) from err

        effective_schedule_type = (
            normalized.get("schedule_type") or current.get("schedule_type") or "cron"
        )
        if effective_schedule_type == "interval":
            normalized["cron_expression"] = None
        elif effective_schedule_type == "cron":
            normalized["interval_seconds"] = None

        return normalized

    async def _call_llm_for_json(self, *, messages: list[dict[str, str]]) -> str:
        response: dict[str, Any] | None = None
        async for chunk in self.llm.chat(
            messages=messages,
            tools=None,
            stream=False,
            temperature=0.0,
            response_format={"type": "json_object"},
        ):
            response = chunk
            break
        if response is None:
            raise ValueError("LLM returned empty response")
        content = (
            ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        ).strip()
        if not content:
            raise ValueError("LLM did not return a structured patch")
        return content

    def _run_async_safely(self, coro: Awaitable[Any]) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        result: dict[str, Any] = {}
        error: dict[str, BaseException] = {}

        def runner() -> None:
            try:
                result["value"] = asyncio.run(coro)
            except BaseException as exc:  # pragma: no cover - forwarded to caller
                error["value"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if "value" in error:
            raise error["value"]
        return result.get("value")

    def _parse_json_patch(self, raw: str) -> dict[str, Any]:
        text = raw.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        try:
            data = json.loads(text)
        except Exception as err:
            raise ValueError(f"LLM patch is not valid JSON: {err}") from err
        if not isinstance(data, dict):
            raise ValueError("LLM patch must be a JSON object")
        return data

    def _summarize(self, patch: dict[str, Any], current: dict[str, Any]) -> str:
        schedule_type = patch.get("schedule_type") or current.get("schedule_type") or "cron"
        if schedule_type == "interval":
            seconds = patch.get("interval_seconds") or current.get("interval_seconds") or 0
            return f"Interval schedule: every {seconds} seconds"
        cron = patch.get("cron_expression") or current.get("cron_expression") or "* * * * *"
        return f"Cron schedule: {cron}"
