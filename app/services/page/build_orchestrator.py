from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.models import models
from app.services.agent.build_verify_loop import (
    BuildAttempt,
    BuildVerifyLoop,
    VerificationOutcome,
)
from app.services.page.authoring_agent import (
    PageAuthoringAgent,
    PageAuthoringResult,
    PageBuildCommand,
    PageVerificationResult,
)
from app.services.page.e2e_verifier import PageE2EVerificationResult, PageE2EVerifier
from app.services.platform.coding_engine import CodingEngineApplyResult
from app.services.platform.workspace_store import WorkspaceStore


@dataclass(frozen=True)
class PageBuildOrchestratorCommand:
    prompt: str
    conversation_context: str
    recent_contexts: list[dict[str, Any]]
    orchestration: dict[str, Any]
    dependency_plan: dict[str, Any]


@dataclass(frozen=True)
class PageBuildOrchestratorResult:
    status: str
    reason: str
    summary: str
    plan_summary: dict[str, Any]
    apply_result: CodingEngineApplyResult | None
    page_verification: PageVerificationResult | None
    e2e_verification: PageE2EVerificationResult | None
    next_draft_payload: dict[str, Any] | None
    attempts: list[dict[str, Any]]


@dataclass(frozen=True)
class _PageBuildAttemptBundle:
    authoring: PageAuthoringResult
    next_draft_payload: dict[str, Any]
    e2e_verification: PageE2EVerificationResult


class PageBuilderOrchestrator:
    def __init__(
        self,
        *,
        authoring_agent: PageAuthoringAgent | None = None,
        e2e_verifier: PageE2EVerifier | None = None,
        runtime_kernel: BuildVerifyLoop | None = None,
    ) -> None:
        self._authoring_agent = authoring_agent or PageAuthoringAgent()
        self._e2e_verifier = e2e_verifier or PageE2EVerifier()
        self._runtime_kernel = runtime_kernel or BuildVerifyLoop(max_attempts=3)

    def execute(
        self,
        *,
        page: models.Page,
        command: PageBuildOrchestratorCommand,
        workspace_store: WorkspaceStore,
        finalize_draft: Callable[[dict[str, Any], CodingEngineApplyResult], dict[str, Any]],
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        existing_functions: list[dict[str, Any]] | None = None,
    ) -> PageBuildOrchestratorResult:
        runtime = self._runtime_kernel.run(
            scope="page.build",
            initial_goal=command.prompt,
            build_step=lambda goal, attempt_index, attempts: self._build_attempt_bundle(
                page=page,
                goal=goal,
                command=command,
                attempts=attempts,
                workspace_store=workspace_store,
                finalize_draft=finalize_draft,
                event_callback=event_callback,
                existing_functions=existing_functions,
            ),
            verify_step=lambda build_result, goal, attempt_index, attempts: (
                self._verify_attempt_bundle(
                    build_result=build_result,
                )
            ),
            summarize_step=lambda build_result: (
                str(
                    build_result.authoring.apply.assistant_message
                    or build_result.authoring.apply.diff_summary
                    or ""
                ).strip()
                if isinstance(build_result, _PageBuildAttemptBundle)
                else ""
            ),
            event_callback=event_callback,
        )
        attempts_payload = [
            {
                "attempt": item.index,
                "status": item.status,
                "summary": item.summary,
                "diagnostics": item.diagnostics,
                "error": item.error,
                "changed_files": item.changed_files,
            }
            for item in runtime.attempts
        ]

        bundle = (
            runtime.final_build_result
            if isinstance(runtime.final_build_result, _PageBuildAttemptBundle)
            else None
        )
        if bundle is None:
            return PageBuildOrchestratorResult(
                status="needs_clarification",
                reason=str(runtime.reason or "page_build_failed"),
                summary="页面构建未产出有效草稿，请补充信息后继续。",
                plan_summary={},
                apply_result=None,
                page_verification=None,
                e2e_verification=None,
                next_draft_payload=None,
                attempts=attempts_payload,
            )

        plan_summary = bundle.authoring.plan.summary
        page_verification = bundle.authoring.verification
        apply_result = bundle.authoring.apply
        next_draft_payload = bundle.next_draft_payload
        e2e_verification = bundle.e2e_verification
        if runtime.status != "done":
            verification_payload = (
                runtime.final_verification.payload
                if runtime.final_verification is not None
                and isinstance(runtime.final_verification.payload, dict)
                else {}
            )
            reason = str(
                verification_payload.get("code") or runtime.reason or "needs_clarification"
            )
            summary = "页面草稿校验未通过，请补充约束后重试。"
            if str(reason).startswith("e2e_"):
                summary = "Function 与 Page 联调未通过，请补充信息后继续。"
            return PageBuildOrchestratorResult(
                status="needs_clarification",
                reason=reason,
                summary=summary,
                plan_summary=plan_summary,
                apply_result=apply_result,
                page_verification=page_verification,
                e2e_verification=e2e_verification,
                next_draft_payload=next_draft_payload,
                attempts=attempts_payload,
            )

        summary = (
            str(apply_result.assistant_message or apply_result.diff_summary or "").strip()
            or "页面草稿已更新。"
        )
        return PageBuildOrchestratorResult(
            status="done",
            reason="apply_done",
            summary=summary,
            plan_summary=plan_summary,
            apply_result=apply_result,
            page_verification=page_verification,
            e2e_verification=e2e_verification,
            next_draft_payload=next_draft_payload,
            attempts=attempts_payload,
        )

    def _build_attempt_bundle(
        self,
        *,
        page: models.Page,
        goal: str,
        command: PageBuildOrchestratorCommand,
        attempts: list[BuildAttempt],
        workspace_store: WorkspaceStore,
        finalize_draft: Callable[[dict[str, Any], CodingEngineApplyResult], dict[str, Any]],
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        existing_functions: list[dict[str, Any]] | None = None,
    ) -> _PageBuildAttemptBundle:
        attempt_contexts = [
            {
                "prompt": item.goal,
                "status": item.status,
                "error": item.error or "; ".join(item.diagnostics[:2]),
                "summary": item.summary,
            }
            for item in reversed(attempts[-4:])
        ]
        merged_contexts = attempt_contexts + [
            item for item in (command.recent_contexts or []) if isinstance(item, dict)
        ]
        authoring = self._authoring_agent.execute(
            page=page,
            command=PageBuildCommand(
                prompt=goal,
                conversation_context=command.conversation_context,
                recent_contexts=merged_contexts,
                orchestration=command.orchestration,
            ),
            workspace_store=workspace_store,
            event_callback=event_callback,
            existing_functions=existing_functions,
        )
        current_draft = page.draft_payload if isinstance(page.draft_payload, dict) else {}
        next_draft_payload = finalize_draft(current_draft, authoring.apply)
        e2e_verification = self._e2e_verifier.verify(
            draft_payload=next_draft_payload,
            dependency_plan=command.dependency_plan,
        )
        return _PageBuildAttemptBundle(
            authoring=authoring,
            next_draft_payload=next_draft_payload,
            e2e_verification=e2e_verification,
        )

    def _verify_attempt_bundle(self, *, build_result: Any) -> VerificationOutcome:
        if not isinstance(build_result, _PageBuildAttemptBundle):
            return VerificationOutcome(
                passed=False,
                diagnostics=["page build attempt payload invalid"],
                payload={"code": "attempt_payload_invalid"},
                summary="attempt_payload_invalid",
            )
        page_verification = build_result.authoring.verification
        if not bool(page_verification.passed):
            diagnostics = [
                str(item) for item in (page_verification.diagnostics or []) if str(item).strip()
            ]
            return VerificationOutcome(
                passed=False,
                diagnostics=diagnostics,
                payload={
                    "code": "page_verification_failed",
                    "page_verification": page_verification,
                    "e2e_verification": build_result.e2e_verification,
                },
                summary="page_verification_failed",
            )
        e2e_verification = build_result.e2e_verification
        if not bool(e2e_verification.passed):
            diagnostics = [
                str(item) for item in (e2e_verification.diagnostics or []) if str(item).strip()
            ]
            return VerificationOutcome(
                passed=False,
                diagnostics=diagnostics,
                payload={
                    "code": "e2e_verification_failed",
                    "page_verification": page_verification,
                    "e2e_verification": e2e_verification,
                },
                summary="e2e_verification_failed",
            )
        return VerificationOutcome(
            passed=True,
            diagnostics=[],
            payload={
                "code": "verification_passed",
                "page_verification": page_verification,
                "e2e_verification": e2e_verification,
            },
            summary="verification_passed",
        )
