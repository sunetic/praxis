from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from app.services.agent.review_contract import ReviewFinding, ReviewResult
from app.services.llm import LLMClient, get_llm_client
from app.services.page.review_evidence import (
    PageReviewEvidencePacket,
    PageSemanticReviewConfig,
    build_page_review_evidence,
)
from app.services.platform.prompt_loader import PromptLoader


class PageReviewAgent:
    def __init__(self, *, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or get_llm_client()

    def review_page(self, *, page: Any, config: PageSemanticReviewConfig) -> ReviewResult:
        packet = build_page_review_evidence(page=page, config=config)
        return self.review_packet(packet=packet)

    def review_packet(self, *, packet: PageReviewEvidencePacket) -> ReviewResult:
        raw = _run_async_safely(self._call_llm(packet=packet))
        parsed = _parse_json_object(raw)
        verdict = _normalize_verdict(parsed.get("verdict"))
        findings_raw = parsed.get("findings") if isinstance(parsed.get("findings"), list) else []
        findings: list[ReviewFinding] = []
        for item in findings_raw[:5]:
            if not isinstance(item, dict):
                continue
            findings.append(
                ReviewFinding(
                    severity=str(item.get("severity") or "medium").strip() or "medium",
                    category=str(item.get("category") or "purpose_drift").strip() or "purpose_drift",
                    summary=str(item.get("summary") or "").strip(),
                    evidence=str(item.get("evidence") or "").strip(),
                    why_it_conflicts_with_purpose=str(item.get("why_it_conflicts_with_purpose") or "").strip(),
                    suggested_fix=str(item.get("suggested_fix") or "").strip(),
                )
            )
        summary = str(parsed.get("summary") or "").strip()
        if not summary:
            summary = "页面语义审查已完成。"
        return ReviewResult(verdict=verdict, summary=summary, findings=findings)

    async def _call_llm(self, *, packet: PageReviewEvidencePacket) -> str:
        payload = {
            **packet.to_payload(),
            "schema": {
                "verdict": "pass | warning | fail",
                "summary": "string",
                "findings": [
                    {
                        "severity": "low | medium | high",
                        "category": "noise | purpose_drift | internal_term_leak | workflow_obstruction | scope_drift | insufficient_evidence | design_violation",
                        "summary": "string",
                        "evidence": "string",
                        "why_it_conflicts_with_purpose": "string",
                        "suggested_fix": "string",
                    }
                ],
            },
        }
        has_design_spec = bool(
            (packet.implementation_evidence.get("design_spec") or "").strip()
        )
        system_content = PromptLoader.render(
            "page/prompts/review_agent.tpl",
            has_design_spec=has_design_spec,
        )
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        response: dict[str, Any] | None = None
        async for chunk in self._llm.chat(
            messages=messages,
            tools=None,
            stream=False,
            temperature=0.1,
            response_format={"type": "json_object"},
        ):
            response = chunk
            break
        if response is None:
            raise ValueError("page semantic review returned empty response")
        content = (((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not content:
            raise ValueError("page semantic review missing content")
        return content


def _normalize_verdict(value: Any) -> str:
    verdict = str(value or "").strip().lower()
    if verdict not in {"pass", "warning", "fail"}:
        return "warning"
    return verdict


def _run_async_safely(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover
            error["value"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "value" in error:
        raise error["value"]
    return result.get("value")


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("page semantic review response must be object")
    return parsed
