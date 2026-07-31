from __future__ import annotations

import asyncio
import json
import re
import threading
from typing import Any, Protocol

from app.services.agent.core import BuildAttemptContext
from app.services.llm import LLMClient, get_llm_client
from app.services.platform.coding_engine import CodingEngineApplyResult
from app.services.platform.workspace_store import WorkspaceStore


class BuildApplyAdapter(Protocol):
    def apply_goal(
        self,
        *,
        workspace_store: WorkspaceStore,
        target: Any,
        goal: str,
    ) -> CodingEngineApplyResult: ...


class _ContinuationIntentAdapter:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm = llm_client or get_llm_client()

    def _resolve_primary_requirement_with_llm(
        self, *, prompt: str, history: list[BuildAttemptContext]
    ) -> str:
        if not history:
            return prompt
        latest_requirement = next((item.prompt for item in history if item.prompt), "")
        if not latest_requirement:
            return prompt
        if not self._should_continue_previous_requirement(prompt=prompt, history=history):
            return prompt
        return latest_requirement

    def _should_continue_previous_requirement(
        self, *, prompt: str, history: list[BuildAttemptContext]
    ) -> bool:
        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            return False
        payload = {
            "user_prompt": normalized_prompt,
            "recent_attempts": [
                {
                    "prompt": str(item.prompt or "").strip(),
                    "status": str(item.status or "").strip(),
                    "error": str(item.error or "").strip(),
                    "summary": str(item.summary or "").strip(),
                }
                for item in history[:4]
                if any([item.prompt, item.status, item.error, item.summary])
            ],
            "schema": {
                "continue_previous": "boolean",
            },
            "constraints": [
                "Return JSON only.",
                "continue_previous=true only when current user input semantically asks to continue/retry previous task.",
                "Do not rely on keyword matching only; infer semantics from context.",
            ],
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You classify whether a build prompt is a continuation intent. "
                    "Decide if the user asks to continue the previous requirement."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            raw = self._run_async_safely(self._call_llm_for_json(messages=messages))
            parsed = self._parse_json_object(raw)
            return bool(parsed.get("continue_previous"))
        except Exception:
            return False

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
            raise ValueError("LLM did not return JSON")
        return content

    def _run_async_safely(self, coro: Any) -> Any:
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

    def _parse_json_object(self, raw: str) -> dict[str, Any]:
        text = str(raw or "").strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("LLM output must be a JSON object")
        return parsed
