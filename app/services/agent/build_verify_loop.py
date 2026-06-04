from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from app.services.agent.core import summarize_build_goal

from app.services.llm import LLMClient, get_llm_client
from app.services.platform.prompt_loader import PromptLoader


@dataclass(frozen=True)
class VerificationOutcome:
    passed: bool
    diagnostics: list[str]
    payload: Any = None
    summary: str = ""


@dataclass(frozen=True)
class BuildAttempt:
    index: int
    goal: str
    status: str
    summary: str
    diagnostics: list[str]
    changed_files: list[str]
    error: str = ""
    code_snapshot: str = ""


@dataclass(frozen=True)
class ReflectionDecision:
    action: str
    reason: str
    next_goal: str = ""
    missing: list[str] | None = None


@dataclass(frozen=True)
class BuildResult:
    status: str
    reason: str
    attempts: list[BuildAttempt]
    final_goal: str
    final_build_result: Any = None
    final_verification: VerificationOutcome | None = None
    reflection: ReflectionDecision | None = None


class AgentBuildStep(Protocol):
    def __call__(self, *, goal: str, attempt_index: int, attempts: list[BuildAttempt]) -> Any:
        ...


class AgentVerifyStep(Protocol):
    def __call__(
        self,
        *,
        build_result: Any,
        goal: str,
        attempt_index: int,
        attempts: list[BuildAttempt],
    ) -> VerificationOutcome:
        ...


def _compose_fix_first_goal(
    *,
    fix_summary: str,
    original_summary: str,
    attempt_index: int,
    max_attempts: int,
    guardrails_tail: str = "",
) -> str:
    parts = [
        f"Fix Target (attempt {attempt_index + 1}/{max_attempts}):",
        fix_summary,
        "",
        "Original Requirement (maintain this intent):",
        original_summary,
    ]
    if guardrails_tail:
        parts.extend(["", guardrails_tail])
    return "\n".join(parts)


def _extract_guardrails_tail(goal: str) -> str:
    marker = "Implementation Guardrails"
    idx = str(goal or "").find(marker)
    if idx < 0:
        return ""
    return goal[idx:].strip()


class ReflectionPlanner:
    def __init__(self, *, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or get_llm_client()

    def decide(
        self,
        *,
        scope: str,
        initial_goal: str,
        current_goal: str,
        attempt_index: int,
        max_attempts: int,
        diagnostics: list[str],
        error: str,
        attempts: list[BuildAttempt],
        code_snapshot: str = "",
    ) -> ReflectionDecision:
        if attempt_index >= max_attempts:
            return ReflectionDecision(
                action="needs_clarification",
                reason="max_attempts_exhausted",
                next_goal="",
                missing=[],
            )
        fix_summary = "; ".join([item for item in diagnostics[:3] if item]) or (error or "verification_failed")
        initial_summary = str(initial_goal or "").strip().split("\n")[0][:200]
        guardrails_tail = _extract_guardrails_tail(initial_goal)
        if os.getenv("PYTEST_CURRENT_TEST"):
            return ReflectionDecision(
                action="retry",
                reason="pytest_fallback_retry",
                next_goal=_compose_fix_first_goal(
                    fix_summary=fix_summary,
                    original_summary=initial_summary,
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    guardrails_tail=guardrails_tail,
                ),
                missing=[],
            )
        try:
            return self._decide_with_llm(
                scope=scope,
                initial_goal=initial_goal,
                current_goal=current_goal,
                attempt_index=attempt_index,
                max_attempts=max_attempts,
                diagnostics=diagnostics,
                error=error,
                attempts=attempts,
                code_snapshot=code_snapshot,
            )
        except Exception:
            return ReflectionDecision(
                action="retry",
                reason="fallback_retry",
                next_goal=_compose_fix_first_goal(
                    fix_summary=fix_summary,
                    original_summary=initial_summary,
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                    guardrails_tail=guardrails_tail,
                ),
                missing=[],
            )

    def _decide_with_llm(
        self,
        *,
        scope: str,
        initial_goal: str,
        current_goal: str,
        attempt_index: int,
        max_attempts: int,
        diagnostics: list[str],
        error: str,
        attempts: list[BuildAttempt],
        code_snapshot: str = "",
    ) -> ReflectionDecision:
        payload = {
            "scope": scope,
            "attempt_index": attempt_index,
            "max_attempts": max_attempts,
            "initial_goal": initial_goal,
            "current_goal": current_goal,
            "last_error": error,
            "last_diagnostics": diagnostics[:8],
            "last_code_snapshot": code_snapshot[:3000] if code_snapshot else "",
            "attempt_history": [
                {
                    "index": item.index,
                    "status": item.status,
                    "summary": item.summary,
                    "diagnostics": item.diagnostics[:4],
                    "error": item.error,
                }
                for item in attempts[-4:]
            ],
            "schema": {
                "action": "retry | needs_clarification",
                "reason": "string",
                "next_goal": "string",
                "missing": ["string"],
            },
        }
        messages = [
            {
                "role": "system",
                "content": PromptLoader.render("agent/prompts/reflection_planner.tpl"),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        raw = _run_async_safely(self._call_llm_json(messages=messages))
        parsed = _parse_json_object(raw)
        action = str(parsed.get("action") or "").strip().lower()
        if action not in {"retry", "needs_clarification"}:
            action = "retry"
        missing = [str(item).strip() for item in (parsed.get("missing") or []) if str(item).strip()]
        return ReflectionDecision(
            action=action,
            reason=str(parsed.get("reason") or "llm_reflection"),
            next_goal=str(parsed.get("next_goal") or ""),
            missing=missing,
        )

    async def _call_llm_json(self, *, messages: list[dict[str, str]]) -> str:
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
            raise ValueError("LLM reflection empty")
        content = (((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not content:
            raise ValueError("LLM reflection missing content")
        return content


class BuildVerifyLoop:
    def __init__(
        self,
        *,
        max_attempts: int = 3,
        reflection_planner: ReflectionPlanner | None = None,
    ) -> None:
        self._max_attempts = max(1, int(max_attempts or 1))
        self._reflection_planner = reflection_planner or ReflectionPlanner()

    def run(
        self,
        *,
        scope: str,
        initial_goal: str,
        build_step: AgentBuildStep,
        verify_step: AgentVerifyStep,
        summarize_step: Callable[[Any], str] | None = None,
        snapshot_step: Callable[[], str] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> BuildResult:
        attempts: list[BuildAttempt] = []
        goal = str(initial_goal or "").strip()
        final_build_result: Any = None
        final_verification: VerificationOutcome | None = None
        final_reflection: ReflectionDecision | None = None

        def emit_event(
            *,
            phase: str,
            status: str,
            summary: str,
            payload: dict[str, Any] | None = None,
        ) -> None:
            if event_callback is None:
                return
            event: dict[str, Any] = {
                "type": "phase",
                "phase": str(phase or "").strip(),
                "status": str(status or "").strip(),
                "summary": str(summary or "").strip(),
                "created_at": datetime.now(UTC).isoformat(),
            }
            if isinstance(payload, dict) and payload:
                event["payload"] = payload
            try:
                event_callback(event)
            except Exception:
                return

        for attempt_index in range(1, self._max_attempts + 1):
            build_result: Any = None
            build_error = ""
            diagnostics: list[str] = []
            _goal_first_line = summarize_build_goal(goal, max_len=160)
            emit_event(
                phase="act",
                status="running",
                summary=_goal_first_line or f"Act - Attempt {attempt_index} running",
                payload={"attempt": attempt_index},
            )
            try:
                build_result = build_step(goal=goal, attempt_index=attempt_index, attempts=list(attempts))
                final_build_result = build_result
            except Exception as exc:
                build_error = str(exc)

            # If the coding engine signals that clarification is needed, short-circuit
            # immediately rather than treating it as a build failure and retrying.
            if build_result is not None and not build_error:
                _engine_status = str(getattr(build_result, "result_status", "completed") or "completed").strip().lower()
                if _engine_status in ("too_complex", "needs_clarification"):
                    _engine_message = str(
                        getattr(build_result, "assistant_message", "") or _engine_status
                    ).strip()
                    emit_event(
                        phase="observe",
                        status="needs_clarification",
                        summary=_engine_message or "More information is needed to complete the build",
                        payload={"attempt": attempt_index, "result_status": _engine_status},
                    )
                    return BuildResult(
                        status="needs_clarification",
                        reason=_engine_status,
                        attempts=attempts,
                        final_goal=goal,
                        final_build_result=build_result,
                        final_verification=None,
                        reflection=None,
                    )

            if build_error:
                verification = VerificationOutcome(
                    passed=False,
                    diagnostics=[build_error],
                    payload={"code": "build_exception", "message": build_error},
                    summary="build_exception",
                )
                emit_event(
                    phase="observe",
                    status="failed",
                    summary=f"Observe - Attempt {attempt_index} build failed: {build_error}",
                    payload={"attempt": attempt_index, "error": build_error},
                )
            else:
                emit_event(
                    phase="observe",
                    status="running",
                    summary=f"Observe - Attempt {attempt_index} verifying",
                    payload={"attempt": attempt_index},
                )
                verification = verify_step(
                    build_result=build_result,
                    goal=goal,
                    attempt_index=attempt_index,
                    attempts=list(attempts),
                )
            final_verification = verification
            diagnostics = [str(item).strip() for item in (verification.diagnostics or []) if str(item).strip()]
            changed_files_raw = getattr(build_result, "changed_files", []) if build_result is not None else []
            changed_files = [str(item) for item in changed_files_raw if str(item).strip()]
            summary = (
                verification.summary
                or (summarize_step(build_result) if (summarize_step is not None and build_result is not None) else "")
                or ("build failed" if build_error else "verification failed")
            )
            current_code_snapshot = snapshot_step() if snapshot_step is not None else ""
            attempts.append(
                BuildAttempt(
                    index=attempt_index,
                    goal=goal,
                    status="done" if verification.passed else "failed",
                    summary=str(summary),
                    diagnostics=diagnostics[:8],
                    changed_files=changed_files[:16],
                    error=build_error,
                    code_snapshot=current_code_snapshot[:4000],
                )
            )
            emit_event(
                phase="observe",
                status="done" if verification.passed else "failed",
                summary=(
                    f"Observe - Attempt {attempt_index} verification passed"
                    if verification.passed
                    else f"Observe - Attempt {attempt_index} verification failed: {(diagnostics[0] if diagnostics else summary)}"
                ),
                payload={
                    "attempt": attempt_index,
                    "diagnostics": diagnostics[:3],
                    "passed": bool(verification.passed),
                },
            )
            if verification.passed:
                return BuildResult(
                    status="done",
                    reason="verification_passed",
                    attempts=attempts,
                    final_goal=goal,
                    final_build_result=build_result,
                    final_verification=verification,
                    reflection=None,
                )

            try:
                reflection = self._reflection_planner.decide(
                    scope=scope,
                    initial_goal=initial_goal,
                    current_goal=goal,
                    attempt_index=attempt_index,
                    max_attempts=self._max_attempts,
                    diagnostics=diagnostics,
                    error=build_error,
                    attempts=attempts,
                    code_snapshot=current_code_snapshot,
                )
            except TypeError as exc:
                if "code_snapshot" not in str(exc):
                    raise
                reflection = self._reflection_planner.decide(
                    scope=scope,
                    initial_goal=initial_goal,
                    current_goal=goal,
                    attempt_index=attempt_index,
                    max_attempts=self._max_attempts,
                    diagnostics=diagnostics,
                    error=build_error,
                    attempts=attempts,
                )
            final_reflection = reflection
            emit_event(
                phase="reflect",
                status="noted",
                summary=f"Verification failed on attempt {attempt_index}; initiating fix retry",
                payload={
                    "attempt": attempt_index,
                    "action": reflection.action,
                    "missing": list(reflection.missing or []),
                    "reason": str(reflection.reason or ""),
                },
            )
            if reflection.action != "retry":
                emit_event(
                    phase="reflect",
                    status="blocked",
                    summary="Additional information is needed to continue",
                    payload={"attempt": attempt_index, "reason": str(reflection.reason or "")},
                )
                return BuildResult(
                    status="needs_clarification",
                    reason=str(reflection.reason or "needs_clarification"),
                    attempts=attempts,
                    final_goal=goal,
                    final_build_result=build_result,
                    final_verification=verification,
                    reflection=reflection,
                )
            next_goal = str(reflection.next_goal or "").strip()
            if not next_goal:
                emit_event(
                    phase="reflect",
                    status="blocked",
                    summary="Reflect - Missing next-round goal; awaiting clarification",
                    payload={"attempt": attempt_index, "reason": "retry_goal_empty"},
                )
                return BuildResult(
                    status="needs_clarification",
                    reason="retry_goal_empty",
                    attempts=attempts,
                    final_goal=goal,
                    final_build_result=build_result,
                    final_verification=verification,
                    reflection=reflection,
                )
            emit_event(
                phase="retry",
                status="running",
                summary=f"Starting retry attempt {attempt_index + 1}",
                payload={"attempt": attempt_index + 1},
            )
            goal = next_goal

        return BuildResult(
            status="needs_clarification",
            reason="max_attempts_exhausted",
            attempts=attempts,
            final_goal=goal,
            final_build_result=final_build_result,
            final_verification=final_verification,
            reflection=final_reflection,
        )


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
        except BaseException as exc:  # pragma: no cover - forwarded to caller
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
        raise ValueError("reflection response must be object")
    return parsed
