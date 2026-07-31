from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.models import models
from app.services.page.chat_agent import PageChatAgent
from app.services.page.review_agent import PageReviewAgent
from app.services.page.review_evidence import normalize_page_semantic_review_config
from app.services.platform.coding_engine import CodingEngineApplyResult
from app.services.platform.workspace_store import WorkspaceStore


@dataclass(frozen=True)
class PageBuildCommand:
    prompt: str
    conversation_context: str = ""
    recent_contexts: list[dict[str, Any]] | None = None
    orchestration: dict[str, Any] | None = None


@dataclass(frozen=True)
class PagePlanResult:
    goal: str
    summary: dict[str, Any]


@dataclass(frozen=True)
class PageVerificationResult:
    passed: bool
    diagnostics: list[str]
    checks: list[dict[str, Any]]
    semantic_review: dict[str, Any] | None = None


@dataclass(frozen=True)
class PageAuthoringResult:
    plan: PagePlanResult
    apply: CodingEngineApplyResult
    verification: PageVerificationResult


class PagePlanner:
    def __init__(self, *, chat_agent: PageChatAgent | None = None) -> None:
        self._chat_agent = chat_agent or PageChatAgent()

    def plan(self, *, command: PageBuildCommand) -> PagePlanResult:
        goal = self._chat_agent.compose_page_build_goal(
            prompt=str(command.prompt or ""),
            recent_contexts=command.recent_contexts,
            conversation_context=str(command.conversation_context or ""),
        )
        summary = {
            "has_conversation_context": bool(str(command.conversation_context or "").strip()),
            "recent_context_count": len(command.recent_contexts or []),
            "goal_preview": str(goal or "").strip()[:240],
        }
        return PagePlanResult(goal=goal, summary=summary)


class PageBuilder:
    def __init__(self, *, chat_agent: PageChatAgent | None = None) -> None:
        self._chat_agent = chat_agent or PageChatAgent()

    def build(
        self,
        *,
        page: models.Page,
        goal: str,
        workspace_store: WorkspaceStore,
        existing_functions: list[dict[str, Any]] | None = None,
    ) -> CodingEngineApplyResult:
        return self._chat_agent.apply_page_goal(
            page=page,
            goal=goal,
            workspace_store=workspace_store,
            existing_functions=existing_functions,
        )


class PageVerifier:
    def __init__(self, *, review_agent: PageReviewAgent | None = None) -> None:
        self._review_agent = review_agent

    def verify(
        self, *, page: models.Page, orchestration: dict[str, Any] | None = None
    ) -> PageVerificationResult:
        payload = page.draft_payload if isinstance(page.draft_payload, dict) else {}
        checks: list[dict[str, Any]] = []
        diagnostics: list[str] = []
        semantic_review: dict[str, Any] | None = None

        version = str(payload.get("version") or "")
        version_ok = version == "page-runtime-v2"
        checks.append(
            {"name": "runtime_version", "passed": version_ok, "value": version or "<empty>"}
        )
        if not version_ok:
            diagnostics.append("draft_payload.version 必须是 page-runtime-v2")

        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
        source_code = str(source.get("code") or "").strip()
        preview_html = str(runtime.get("preview_html") or "").strip()

        source_ok = bool(source_code)
        runtime_ok = bool(preview_html)
        checks.append({"name": "source_code_non_empty", "passed": source_ok})
        checks.append({"name": "preview_html_non_empty", "passed": runtime_ok})

        if not source_ok:
            diagnostics.append("source.code 不能为空")
        if not runtime_ok:
            diagnostics.append("runtime.preview_html 不能为空")

        review_config = normalize_page_semantic_review_config(orchestration)
        if review_config is not None:
            required_review_fields = {
                "page_purpose": bool(review_config.page_purpose),
                "primary_workflow": len(review_config.primary_workflow) > 0,
                "anti_goals": len(review_config.anti_goals) > 0,
            }
            review_inputs_ok = all(required_review_fields.values())
            checks.append(
                {
                    "name": "semantic_review_inputs",
                    "passed": review_inputs_ok,
                    "value": required_review_fields,
                }
            )
            if not review_inputs_ok:
                diagnostics.append(
                    "semantic_review 缺少必要输入：page_purpose / primary_workflow / anti_goals"
                )
            elif source_ok and runtime_ok:
                try:
                    review_agent = self._review_agent or PageReviewAgent()
                    review_result = review_agent.review_page(page=page, config=review_config)
                    semantic_review = review_result.to_payload()
                    verdict_passed = str(review_result.verdict) != "fail"
                    checks.append(
                        {
                            "name": "semantic_review_verdict",
                            "passed": verdict_passed,
                            "value": str(review_result.verdict),
                            "finding_count": len(review_result.findings),
                            "summary": str(review_result.summary),
                        }
                    )
                    if not verdict_passed:
                        diagnostics.append(str(review_result.summary or "页面语义审查未通过"))
                        for item in review_result.findings[:2]:
                            if item.summary:
                                diagnostics.append(item.summary)
                except Exception as exc:
                    checks.append(
                        {
                            "name": "semantic_review_verdict",
                            "passed": False,
                            "value": "error",
                            "error": str(exc),
                        }
                    )
                    diagnostics.append(f"页面语义审查失败：{exc}")

        return PageVerificationResult(
            passed=all(bool(item.get("passed")) for item in checks),
            diagnostics=diagnostics,
            checks=checks,
            semantic_review=semantic_review,
        )


class PageAuthoringAgent:
    def __init__(
        self,
        *,
        planner: PagePlanner | None = None,
        builder: PageBuilder | None = None,
        verifier: PageVerifier | None = None,
    ) -> None:
        self._planner = planner or PagePlanner()
        self._builder = builder or PageBuilder()
        self._verifier = verifier or PageVerifier()

    def execute(
        self,
        *,
        page: models.Page,
        command: PageBuildCommand,
        workspace_store: WorkspaceStore,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        existing_functions: list[dict[str, Any]] | None = None,
    ) -> PageAuthoringResult:
        plan = self._planner.plan(command=command)
        if event_callback is not None:
            goal_text = str(plan.goal or "").strip()
            goal_preview = goal_text.split("\n")[0][:200] if goal_text else ""
            _emit_authoring_plan_event(
                event_callback, summary=goal_preview or "需求规划 · 进入构建阶段"
            )
        apply = self._builder.build(
            page=page,
            goal=plan.goal,
            workspace_store=workspace_store,
            existing_functions=existing_functions,
        )
        verification = self._verifier.verify(page=page, orchestration=command.orchestration)
        return PageAuthoringResult(plan=plan, apply=apply, verification=verification)


def _emit_authoring_plan_event(
    event_callback: Callable[[dict[str, Any]], None],
    *,
    summary: str,
) -> None:
    try:
        event_callback(
            {
                "type": "phase",
                "phase": "plan",
                "status": "done",
                "summary": summary,
                "created_at": datetime.now(UTC).isoformat(),
                "payload": {"source": "llm", "agent": "PagePlanner"},
            }
        )
    except Exception:
        pass
