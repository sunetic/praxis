from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class CodingEngineEdit:
    relative_path: str
    content: str


@dataclass(frozen=True)
class CodingEnginePlan:
    goal: str
    allowed_files: list[str]
    edits: list[CodingEngineEdit] = field(default_factory=list)


@dataclass(frozen=True)
class CodingEngineApplyResult:
    changed_files: list[str]
    diff_summary: str
    tests_suggested: list[str]
    risk_notes: list[str]
    assistant_message: str
    generated_title: str = ""
    generated_description: str = ""
    result_status: str = "completed"  # completed | needs_clarification | too_complex


class CodingEngineAdapter(Protocol):
    def plan_changes(
        self,
        *,
        goal: str,
        allowed_files: list[str],
        edits: list[CodingEngineEdit],
    ) -> CodingEnginePlan: ...

    def apply_changes(
        self, *, workspace_dir: Path, plan: CodingEnginePlan
    ) -> CodingEngineApplyResult: ...


def _apply_prepared_edits(*, workspace_dir: Path, plan: CodingEnginePlan) -> list[str]:
    workspace_resolved = workspace_dir.resolve()
    allowed = {str(item or "").strip() for item in plan.allowed_files if str(item or "").strip()}
    changed_files: list[str] = []
    for edit in plan.edits:
        relative = str(edit.relative_path or "").strip()
        if not relative:
            continue
        if relative not in allowed:
            raise ValueError(f"File {relative} is outside allowed_files scope")
        target = (workspace_resolved / relative).resolve()
        if not str(target).startswith(str(workspace_resolved)):
            raise ValueError("Edit target escapes workspace directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(edit.content, encoding="utf-8")
        changed_files.append(relative)
    return changed_files


def _run_async_safely(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, object] = {}
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


class BuiltinReasoningAdapter:
    """
    Python-native coding adapter backed by the shared ReasoningEngine.

    Behavior:
    - If `plan.edits` is provided, apply them directly (deterministic path).
    - If `plan.edits` is empty, run the shared reasoning/tool loop in workspace.
    """

    engine_name = "reasoning"

    def __init__(self, *, max_iterations: int = 12) -> None:
        self.max_iterations = max_iterations
        self._event_callback: Any | None = None

    def set_event_callback(self, callback: Any | None) -> None:
        self._event_callback = callback

    def plan_changes(
        self,
        *,
        goal: str,
        allowed_files: list[str],
        edits: list[CodingEngineEdit],
    ) -> CodingEnginePlan:
        return CodingEnginePlan(goal=goal, allowed_files=allowed_files, edits=edits)

    def apply_changes(
        self, *, workspace_dir: Path, plan: CodingEnginePlan
    ) -> CodingEngineApplyResult:
        if plan.edits:
            changed_files = _apply_prepared_edits(workspace_dir=workspace_dir, plan=plan)
            return CodingEngineApplyResult(
                changed_files=changed_files,
                diff_summary=f"Applied {len(changed_files)} file change(s)",
                tests_suggested=[],
                risk_notes=[],
                assistant_message="Code changes applied to workspace.",
            )

        from app.services.coding.reasoning_agent import CodingReasoningAgent

        agent = CodingReasoningAgent(
            max_iterations=self.max_iterations,
            event_callback=self._event_callback,
        )
        result = _run_async_safely(
            agent.run(
                goal=plan.goal,
                workspace_dir=workspace_dir,
                allowed_files=plan.allowed_files,
            )
        )
        if not isinstance(result, CodingEngineApplyResult):
            raise ValueError("reasoning coding adapter returned invalid result")
        return CodingEngineApplyResult(
            changed_files=list(result.changed_files),
            diff_summary=str(result.diff_summary),
            tests_suggested=list(result.tests_suggested),
            risk_notes=list(result.risk_notes),
            assistant_message=str(result.assistant_message),
            generated_title=str(result.generated_title or ""),
            generated_description=str(result.generated_description or ""),
            result_status=str(result.result_status or "completed"),
        )
