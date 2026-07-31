from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.models import models
from app.services.agent.build_verify_loop import (
    BuildVerifyLoop,
    VerificationOutcome,
)
from app.services.function.chat_agent import FunctionChatAgent
from app.services.function.runtime import FunctionRuntimeService
from app.services.function.schema_probe import FunctionSchemaProbe
from app.services.function.strategy import FunctionStrategyDecider, FunctionVerificationHarness
from app.services.platform.coding_engine import CodingEngineApplyResult
from app.services.platform.workspace_store import WorkspaceStore


@dataclass(frozen=True)
class FunctionBuildPlanResult:
    goal: str
    strategy_decision: dict[str, Any]


class FunctionPlanner:
    def __init__(
        self,
        *,
        strategy_decider: FunctionStrategyDecider | None = None,
        chat_agent: FunctionChatAgent | None = None,
    ) -> None:
        self._strategy_decider = strategy_decider or FunctionStrategyDecider()
        self._chat_agent = chat_agent or FunctionChatAgent()

    def plan(
        self,
        *,
        db: Session,
        function: models.Function,
        prompt: str,
        conversation_context: str,
        skill_context: str = "",
        recent_contexts: list[dict[str, Any]],
        requirement_contract: dict[str, Any] | None,
    ) -> FunctionBuildPlanResult:
        strategy_decision = self._strategy_decider.decide(
            db,
            requirement_text=str(prompt or function.description or function.name or ""),
            contract=requirement_contract if requirement_contract else None,
            exclude_function_id=function.id,
        )
        goal = self._chat_agent.compose_function_build_goal(
            prompt=prompt,
            recent_contexts=recent_contexts,
            conversation_context=conversation_context,
            skill_context=skill_context,
        )
        return FunctionBuildPlanResult(goal=goal, strategy_decision=strategy_decision)


class FunctionBuilderAgent:
    def __init__(self, *, chat_agent: FunctionChatAgent | None = None) -> None:
        self._chat_agent = chat_agent or FunctionChatAgent()

    def build(
        self,
        *,
        function: models.Function,
        goal: str,
        workspace_store: WorkspaceStore,
        datasource_schema: dict[str, Any] | None = None,
        datasource_id: int | None = None,
    ) -> CodingEngineApplyResult:
        return self._chat_agent.apply_function_goal(
            function=function,
            goal=goal,
            workspace_store=workspace_store,
            datasource_schema=datasource_schema,
            datasource_id=datasource_id,
        )


class FunctionVerifier:
    _PAYLOAD_KEY_REQUIRED_RE = re.compile(r"payload\[['\"]([a-zA-Z0-9_]+)['\"]\]")
    _PAYLOAD_KEY_GET_RE = re.compile(r"payload\.get\(\s*['\"]([a-zA-Z0-9_]+)['\"]")
    _PAYLOAD_INT_CAST_RE = re.compile(
        r"(?:int|_safe_int)\(\s*payload(?:\.get\(\s*['\"]([a-zA-Z0-9_]+)['\"]|"
        r"\[['\"]([a-zA-Z0-9_]+)['\"]\])"
    )
    _PAYLOAD_FLOAT_CAST_RE = re.compile(
        r"float\(\s*payload(?:\.get\(\s*['\"]([a-zA-Z0-9_]+)['\"]|"
        r"\[['\"]([a-zA-Z0-9_]+)['\"]\])"
    )
    _RUNTIME_ENVIRONMENT_FAILURE_PATTERNS = (
        "an attempt has been made to start a new process before the",
        "safe importing of main module",
        "freeze_support()",
        "brokenprocesspool",
        "<stdin>",
        "__main__.py",
    )

    def __init__(
        self,
        *,
        harness: FunctionVerificationHarness | None = None,
        runtime_service_factory: Callable[[], FunctionRuntimeService] | None = None,
    ) -> None:
        self._harness = harness or FunctionVerificationHarness()
        self._runtime_service_factory = runtime_service_factory

    def verify(
        self,
        *,
        function: models.Function,
        db: Session | None = None,
        schema_probe: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        report = self._harness.verify_draft(
            code_snapshot=str(function.draft_code or ""),
            dependency_manifest=function.draft_dependencies
            if isinstance(function.draft_dependencies, dict)
            else {},
        )
        runtime_report = self._verify_runtime_samples(
            function=function, db=db, schema_probe=schema_probe
        )
        report["runtime_verification"] = runtime_report
        checks = report.get("checks")
        if isinstance(checks, list):
            for item in runtime_report.get("checks") or []:
                if isinstance(item, dict):
                    checks.append(item)
        diagnostics = report.get("diagnostics")
        if isinstance(diagnostics, list):
            diagnostics.extend(
                [
                    str(item)
                    for item in (runtime_report.get("diagnostics") or [])
                    if str(item).strip()
                ]
            )
        if (
            bool(report.get("passed"))
            and bool(runtime_report.get("enforced"))
            and not bool(runtime_report.get("passed"))
        ):
            report["passed"] = False
        return report

    def _verify_runtime_samples(
        self,
        *,
        function: models.Function,
        db: Session | None,
        schema_probe: dict[str, Any] | None,
    ) -> dict[str, Any]:
        code_snapshot = str(function.draft_code or "")
        if not code_snapshot.strip():
            return {
                "executed": False,
                "enforced": False,
                "passed": False,
                "reason": "draft_code_empty",
                "checks": [],
                "diagnostics": [],
                "samples": [],
            }
        if db is None:
            return {
                "executed": False,
                "enforced": False,
                "passed": True,
                "reason": "db_unavailable",
                "checks": [],
                "diagnostics": [],
                "samples": [],
            }

        datasource_id = None
        if isinstance(schema_probe, dict):
            raw_ds = schema_probe.get("datasource_id")
            if isinstance(raw_ds, int) and raw_ds > 0:
                datasource_id = raw_ds
        if datasource_id is None and "db." in code_snapshot:
            return {
                "executed": False,
                "enforced": False,
                "passed": True,
                "reason": "probe_datasource_missing_for_db_calls",
                "checks": [],
                "diagnostics": [],
                "samples": [],
            }

        success_payload, failure_payload, payload_meta = self._synthesize_runtime_payloads(
            function=function,
            fallback_datasource_id=datasource_id,
        )
        runtime_service = self._create_runtime_service(db=db)
        checks: list[dict[str, Any]] = []
        diagnostics: list[str] = []
        samples: list[dict[str, Any]] = []
        success_ok = False
        failure_required = failure_payload is not None
        failure_ok = not failure_required
        executed = False
        try:
            success_result = _run_async_safely(
                runtime_service.invoke(
                    function,
                    success_payload,
                    runtime_path="draft",
                    datasource_id=datasource_id,
                    scope_metadata={"execution_mode": "plan", "write_mode": "readonly"},
                    timeout_seconds=8.0,
                )
            )
            success_status = str(success_result.status or "").strip().lower()
            success_error_message = str(getattr(success_result, "error_message", "") or "").strip()
            if self._is_runtime_environment_unavailable(success_error_message):
                return self._runtime_unavailable_report(
                    reason="runtime_environment_unavailable",
                    message=success_error_message,
                    checks=checks,
                    diagnostics=diagnostics,
                    samples=[
                        {
                            "name": "success_sample",
                            "payload": success_payload,
                            "status": success_status,
                            "error_message": success_error_message,
                            "duration_ms": int(getattr(success_result, "duration_ms", 0) or 0),
                        }
                    ],
                    payload_meta=payload_meta,
                    datasource_id=datasource_id,
                )
            success_ok = success_status == "success"
            executed = True
            samples.append(
                {
                    "name": "success_sample",
                    "payload": success_payload,
                    "status": success_status,
                    "error_message": success_error_message,
                    "duration_ms": int(getattr(success_result, "duration_ms", 0) or 0),
                }
            )
            checks.append(
                {
                    "name": "runtime_success_sample",
                    "passed": success_ok,
                    "detail": "Draft runtime invocation with synthesized success payload should succeed.",
                }
            )
            if not success_ok:
                diagnostics.append(
                    "Runtime sample (success) did not pass: "
                    + (
                        str(success_result.error_message or "").strip()
                        or f"status={success_status or 'unknown'}"
                    )
                )

            if failure_required and failure_payload is not None:
                failure_result = _run_async_safely(
                    runtime_service.invoke(
                        function,
                        failure_payload,
                        runtime_path="draft",
                        datasource_id=datasource_id,
                        scope_metadata={"execution_mode": "plan", "write_mode": "readonly"},
                        timeout_seconds=8.0,
                    )
                )
                failure_status = str(failure_result.status or "").strip().lower()
                failure_error_message = str(
                    getattr(failure_result, "error_message", "") or ""
                ).strip()
                if self._is_runtime_environment_unavailable(failure_error_message):
                    samples.append(
                        {
                            "name": "failure_sample",
                            "payload": failure_payload,
                            "status": failure_status,
                            "error_message": failure_error_message,
                            "duration_ms": int(getattr(failure_result, "duration_ms", 0) or 0),
                        }
                    )
                    return self._runtime_unavailable_report(
                        reason="runtime_environment_unavailable",
                        message=failure_error_message,
                        checks=checks,
                        diagnostics=diagnostics,
                        samples=samples,
                        payload_meta=payload_meta,
                        datasource_id=datasource_id,
                    )
                failure_ok = failure_status == "failed"
                samples.append(
                    {
                        "name": "failure_sample",
                        "payload": failure_payload,
                        "status": failure_status,
                        "error_message": failure_error_message,
                        "duration_ms": int(getattr(failure_result, "duration_ms", 0) or 0),
                    }
                )
                checks.append(
                    {
                        "name": "runtime_failure_sample",
                        "passed": failure_ok,
                        "detail": "Draft runtime invocation with synthesized failure payload should fail.",
                    }
                )
                if not failure_ok:
                    diagnostics.append(
                        "Runtime sample (failure) did not trigger failure: "
                        + (
                            str(failure_result.error_message or "").strip()
                            or f"status={failure_status or 'unknown'}"
                        )
                    )
            else:
                checks.append(
                    {
                        "name": "runtime_failure_sample",
                        "passed": True,
                        "detail": "Skipped: no reliable required-field candidate for failure sample synthesis.",
                    }
                )
                samples.append(
                    {
                        "name": "failure_sample",
                        "payload": None,
                        "status": "skipped",
                        "error_message": "",
                        "duration_ms": 0,
                    }
                )
        except Exception as exc:
            if self._is_runtime_environment_unavailable(str(exc)):
                return self._runtime_unavailable_report(
                    reason="runtime_environment_unavailable",
                    message=str(exc),
                    checks=checks,
                    diagnostics=diagnostics,
                    samples=samples,
                    payload_meta=payload_meta,
                    datasource_id=datasource_id,
                )
            diagnostics.append(f"Runtime sample verification exception: {str(exc)}")
            checks.append(
                {
                    "name": "runtime_sample_execution",
                    "passed": False,
                    "detail": str(exc),
                }
            )
        finally:
            executor = getattr(runtime_service, "_executor", None)
            shutdown = getattr(executor, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass

        passed = success_ok and failure_ok
        return {
            "executed": executed,
            "enforced": executed,
            "passed": passed,
            "reason": "ok" if passed else "runtime_sample_failed",
            "checks": checks,
            "diagnostics": diagnostics,
            "samples": samples,
            "payload_meta": payload_meta,
            "datasource_id": datasource_id,
        }

    def _is_runtime_environment_unavailable(self, message: str) -> bool:
        normalized = str(message or "").strip().casefold()
        if not normalized:
            return False
        return any(pattern in normalized for pattern in self._RUNTIME_ENVIRONMENT_FAILURE_PATTERNS)

    def _create_runtime_service(self, *, db: Session | None) -> FunctionRuntimeService:
        if self._runtime_service_factory is not None:
            return self._runtime_service_factory()
        if db is not None:
            bind = db.get_bind()
            local_factory: sessionmaker[Session] = sessionmaker(
                bind=bind,
                autocommit=False,
                autoflush=False,
                expire_on_commit=False,
            )
            return FunctionRuntimeService(session_factory=local_factory, max_workers=1)
        return FunctionRuntimeService(max_workers=1)

    def _runtime_unavailable_report(
        self,
        *,
        reason: str,
        message: str,
        checks: list[dict[str, Any]],
        diagnostics: list[str],
        samples: list[dict[str, Any]],
        payload_meta: dict[str, Any],
        datasource_id: int | None,
    ) -> dict[str, Any]:
        info = str(message or "").strip() or "runtime environment unavailable"
        checks.append(
            {
                "name": "runtime_sample_execution",
                "passed": True,
                "detail": "Skipped enforcement: runtime environment unavailable for sample execution.",
            }
        )
        diagnostics.append(f"Runtime sample verification downgraded: {info}")
        return {
            "executed": False,
            "enforced": False,
            "passed": True,
            "reason": reason,
            "checks": checks,
            "diagnostics": diagnostics,
            "samples": samples,
            "payload_meta": payload_meta,
            "datasource_id": datasource_id,
        }

    def _synthesize_runtime_payloads(
        self,
        *,
        function: models.Function,
        fallback_datasource_id: int | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
        dependencies = (
            function.draft_dependencies if isinstance(function.draft_dependencies, dict) else {}
        )
        builder_spec = (
            dependencies.get("builder_spec")
            if isinstance(dependencies.get("builder_spec"), dict)
            else {}
        )
        input_contract = (
            builder_spec.get("input_contract")
            if isinstance(builder_spec.get("input_contract"), list)
            else []
        )
        success: dict[str, Any] = {}
        required_names: list[str] = []
        inferred_types = self._infer_payload_value_types(str(function.draft_code or ""))
        inferred_required = self._infer_required_payload_keys(str(function.draft_code or ""))
        inferred_optional = self._infer_optional_payload_keys(str(function.draft_code or ""))

        for item in input_contract:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            value_type = (
                str(item.get("type") or inferred_types.get(name.casefold()) or "string")
                .strip()
                .lower()
            )
            success[name] = self._sample_value_for_key(
                key=name,
                value_type=value_type,
                fallback_datasource_id=fallback_datasource_id,
            )
            if bool(item.get("required")):
                required_names.append(name)

        for name in inferred_required:
            if name not in success:
                success[name] = self._sample_value_for_key(
                    key=name,
                    value_type=inferred_types.get(name.casefold(), "string"),
                    fallback_datasource_id=fallback_datasource_id,
                )
            if name not in required_names:
                required_names.append(name)

        for name in inferred_optional:
            if name not in success:
                success[name] = self._sample_value_for_key(
                    key=name,
                    value_type=inferred_types.get(name.casefold(), "string"),
                    fallback_datasource_id=fallback_datasource_id,
                )

        if not success:
            success = {"limit": 20, "filters": {}}
        failure: dict[str, Any] | None = None
        removed_key = ""
        if required_names:
            removed_key = required_names[0]
            failure = {k: v for k, v in success.items() if k != removed_key}
            if not failure:
                failure = None
        meta = {
            "required_keys": required_names,
            "removed_key_for_failure_sample": removed_key,
            "input_contract_count": len(input_contract),
        }
        return success, failure, meta

    def _sample_value_for_key(
        self, *, key: str, value_type: str, fallback_datasource_id: int | None
    ) -> Any:
        normalized = key.casefold()
        if normalized in {"datasource_id", "datasourceid"} or normalized.endswith("_datasource_id"):
            return int(fallback_datasource_id or 1)
        if normalized.endswith("_id"):
            return 1
        if normalized.endswith(
            ("_days", "_hours", "_minutes", "_minute", "_count", "_size", "_index", "_limit")
        ):
            return 1
        if normalized in {"limit", "page_size", "pagesize"}:
            return 20
        if normalized in {"offset", "page"}:
            return 0
        if normalized in {"filters", "where"}:
            return {}
        if normalized in {"dry_run", "dryrun"}:
            return True
        if normalized.endswith("_seconds") or normalized in {"timeout", "retention_seconds"}:
            return 3600
        if value_type in {"integer", "int", "number", "float"}:
            return 1
        if value_type in {"boolean", "bool"}:
            return True
        if value_type in {"array", "list"}:
            return []
        if value_type in {"object", "dict", "map"}:
            return {}
        return "sample"

    def _infer_payload_value_types(self, code_snapshot: str) -> dict[str, str]:
        inferred: dict[str, str] = {}
        for patterns, inferred_type in (
            (self._PAYLOAD_INT_CAST_RE.findall(code_snapshot or ""), "integer"),
            (self._PAYLOAD_FLOAT_CAST_RE.findall(code_snapshot or ""), "float"),
        ):
            for groups in patterns:
                key = next((item.strip() for item in groups if item and item.strip()), "")
                if key and key.casefold() not in inferred:
                    inferred[key.casefold()] = inferred_type
        return inferred

    def _infer_required_payload_keys(self, code_snapshot: str) -> list[str]:
        matches = [
            item.strip()
            for item in self._PAYLOAD_KEY_REQUIRED_RE.findall(code_snapshot or "")
            if item.strip()
        ]
        seen: set[str] = set()
        ordered: list[str] = []
        for key in matches:
            norm = key.casefold()
            if norm in seen:
                continue
            seen.add(norm)
            ordered.append(key)
        return ordered

    def _infer_optional_payload_keys(self, code_snapshot: str) -> list[str]:
        matches = [
            item.strip()
            for item in self._PAYLOAD_KEY_GET_RE.findall(code_snapshot or "")
            if item.strip()
        ]
        seen: set[str] = set()
        ordered: list[str] = []
        for key in matches:
            norm = key.casefold()
            if norm in seen:
                continue
            seen.add(norm)
            ordered.append(key)
        return ordered


@dataclass(frozen=True)
class FunctionBuildOrchestratorResult:
    status: str
    reason: str
    plan: FunctionBuildPlanResult
    apply_result: CodingEngineApplyResult | None
    verification: dict[str, Any]
    attempts: list[dict[str, Any]]
    schema_probe: dict[str, Any]


class FunctionBuildOrchestrator:
    def __init__(
        self,
        *,
        planner: FunctionPlanner | None = None,
        builder: FunctionBuilderAgent | None = None,
        verifier: FunctionVerifier | None = None,
        schema_probe: FunctionSchemaProbe | None = None,
        runtime_kernel: BuildVerifyLoop | None = None,
    ) -> None:
        self._planner = planner or FunctionPlanner()
        self._builder = builder or FunctionBuilderAgent()
        self._verifier = verifier or FunctionVerifier()
        self._schema_probe = schema_probe or FunctionSchemaProbe()
        self._runtime_kernel = runtime_kernel or BuildVerifyLoop(max_attempts=3)

    @staticmethod
    def _make_event_emitter(
        event_callback: Callable[[dict[str, Any]], None] | None,
    ) -> Callable[..., None]:
        from datetime import datetime

        def _emit(*, phase: str, status: str, summary: str) -> None:
            if event_callback is None:
                return
            try:
                event_callback(
                    {
                        "type": "phase",
                        "phase": phase,
                        "status": status,
                        "summary": summary,
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )
            except Exception:
                pass

        return _emit

    def plan(
        self,
        *,
        db: Session,
        function: models.Function,
        prompt: str,
        conversation_context: str,
        skill_context: str = "",
        recent_contexts: list[dict[str, Any]],
        requirement_contract: dict[str, Any] | None,
    ) -> FunctionBuildPlanResult:
        return self._planner.plan(
            db=db,
            function=function,
            prompt=prompt,
            conversation_context=conversation_context,
            skill_context=skill_context,
            recent_contexts=recent_contexts,
            requirement_contract=requirement_contract,
        )

    def execute(
        self,
        *,
        db: Session | None,
        function: models.Function,
        goal: str,
        workspace_store: WorkspaceStore,
        strategy_decision: dict[str, Any],
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> FunctionBuildOrchestratorResult:
        _emit = self._make_event_emitter(event_callback)
        _emit(
            phase="act",
            status="running",
            summary="Probing datasource schema, preparing code generation...",
        )
        probe = self._schema_probe.probe(db=db, requirement_text=goal)
        effective_goal = self._compose_goal_with_probe(goal=goal, probe_context=probe.goal_context)
        workspace_store.set_adapter_event_callback(event_callback)
        runtime = self._runtime_kernel.run(
            scope="function.build",
            initial_goal=effective_goal,
            build_step=lambda goal, attempt_index, attempts: self._builder.build(
                function=function,
                goal=self._compose_goal_with_probe(goal=goal, probe_context=probe.goal_context),
                workspace_store=workspace_store,
                datasource_schema={"tables": probe.columns_by_table}
                if probe.columns_by_table
                else None,
                datasource_id=probe.datasource_id,
            ),
            verify_step=lambda build_result, goal, attempt_index, attempts: self._verify_attempt(
                function=function,
                db=db,
                schema_probe=probe.as_payload(),
            ),
            summarize_step=lambda build_result: str(
                getattr(build_result, "assistant_message", "")
                or getattr(build_result, "diff_summary", "")
            ).strip(),
            snapshot_step=lambda: str(function.draft_code or ""),
            event_callback=event_callback,
        )
        apply_result = (
            runtime.final_build_result
            if isinstance(runtime.final_build_result, CodingEngineApplyResult)
            else None
        )
        verification_payload = (
            runtime.final_verification.payload if runtime.final_verification is not None else {}
        )
        verification = verification_payload if isinstance(verification_payload, dict) else {}
        if runtime.final_verification is not None and "passed" not in verification:
            verification["passed"] = bool(runtime.final_verification.passed)
        if runtime.final_verification is not None and "diagnostics" not in verification:
            verification["diagnostics"] = list(runtime.final_verification.diagnostics or [])
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
        return FunctionBuildOrchestratorResult(
            status=runtime.status,
            reason=runtime.reason,
            plan=FunctionBuildPlanResult(goal=goal, strategy_decision=strategy_decision),
            apply_result=apply_result,
            verification=verification,
            attempts=attempts_payload,
            schema_probe=probe.as_payload(),
        )

    def _verify_attempt(
        self,
        *,
        function: models.Function,
        db: Session | None = None,
        schema_probe: dict[str, Any] | None = None,
    ) -> VerificationOutcome:
        verification = self._verifier.verify(function=function, db=db, schema_probe=schema_probe)
        diagnostics = [
            str(item) for item in (verification.get("diagnostics") or []) if str(item).strip()
        ]
        return VerificationOutcome(
            passed=bool(verification.get("passed")),
            diagnostics=diagnostics,
            payload=verification,
            summary="verification_passed"
            if bool(verification.get("passed"))
            else "verification_failed",
        )

    def _compose_goal_with_probe(self, *, goal: str, probe_context: str) -> str:
        if not str(probe_context or "").strip():
            return goal
        return f"{goal}\n\n{probe_context}"


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


# ── Staged Build ──────────────────────────────────────────────────────────────

_STAGE_1_PROMPT_TEMPLATE = """\
[Stage 1 — Complexity Assessment]

Evaluate whether the following goal can be fulfilled by a SINGLE Praxis Function.

Goal:
{goal}

Instructions:
- Do NOT write any code.
- Respond in JSON only.
- If the goal is clear and scoped to a single concern, return:
  {{"result_status": "clear", "result": "<one-sentence confirmation of what this function will do>"}}
- If the goal is too broad, covers multiple unrelated concerns, or would require more than one function, return:
  {{"result_status": "too_complex", "result": "<explanation + 2-3 suggested sub-goals>"}}
- If essential information is missing (e.g. which datasource, which time range, expected output shape), return:
  {{"result_status": "needs_clarification", "result": "<specific questions to ask the user>"}}
"""

_STAGE_2_PROMPT_TEMPLATE = """\
[Stage 2 — Requirement Refinement]

Refine the implementation specification for a Praxis Function based on the goal below.
Stage 1 assessment: {stage1_result}

Goal:
{goal}

Instructions:
- Do NOT write any code.
- Respond in JSON only.
- Identify the exact tables and columns needed from the available schema.
- Determine the output shape (what the caller expects to receive as a dict).
- Note edge cases: empty result sets, missing datasource, permission errors.
- Return:
  {{"result_status": "refined", "result": "<structured implementation spec as plain text>", "output_shape": "<description of the return dict>"}}
"""


@dataclass(frozen=True)
class StagedBuildStageResult:
    stage: int
    result_status: str  # clear | too_complex | needs_clarification | refined | completed | error
    assistant_message: str
    result_text: str = ""


@dataclass
class StagedBuildResult:
    status: str  # done | needs_clarification | too_complex | failed
    reason: str
    final_stage: int
    stage_results: list[StagedBuildStageResult] = field(default_factory=list)
    refined_goal: str = ""
    apply_result: CodingEngineApplyResult | None = None
    verification: dict[str, Any] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    schema_probe: dict[str, Any] = field(default_factory=dict)


class StagedFunctionBuildOrchestrator:
    """
    Orchestrates Function Build as a sequence of independent coding-engine calls:

    Stage 1 — Complexity Assessment: engine returns JSON analysis, no code written.
    Stage 2 — Requirement Refinement: engine returns refined spec, no code written.
    Stage 4 — Implementation: delegates to BuildVerifyLoop (existing flow).
    Stage 5 — Verification: part of the Kernel loop.

    Stage 3 (implementation plan) is folded into Stage 4's initial prompt.
    """

    def __init__(
        self,
        *,
        planner: FunctionPlanner | None = None,
        builder: FunctionBuilderAgent | None = None,
        verifier: FunctionVerifier | None = None,
        schema_probe: FunctionSchemaProbe | None = None,
        runtime_kernel: BuildVerifyLoop | None = None,
    ) -> None:
        self._planner = planner or FunctionPlanner()
        self._builder = builder or FunctionBuilderAgent()
        self._verifier = verifier or FunctionVerifier()
        self._schema_probe = schema_probe or FunctionSchemaProbe()
        self._runtime_kernel = runtime_kernel or BuildVerifyLoop(max_attempts=3)

    def plan(
        self,
        *,
        db: Session,
        function: models.Function,
        prompt: str,
        conversation_context: str,
        skill_context: str = "",
        recent_contexts: list[dict[str, Any]],
        requirement_contract: dict[str, Any] | None,
    ) -> FunctionBuildPlanResult:
        return self._planner.plan(
            db=db,
            function=function,
            prompt=prompt,
            conversation_context=conversation_context,
            skill_context=skill_context,
            recent_contexts=recent_contexts,
            requirement_contract=requirement_contract,
        )

    def execute(
        self,
        *,
        db: Session | None,
        function: models.Function,
        goal: str,
        workspace_store: WorkspaceStore,
        strategy_decision: dict[str, Any],
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> StagedBuildResult:
        from datetime import datetime

        stage_results: list[StagedBuildStageResult] = []

        def _emit(
            *, phase: str, status: str, summary: str, payload: dict[str, Any] | None = None
        ) -> None:
            if event_callback is None:
                return
            event: dict[str, Any] = {
                "type": "phase",
                "phase": phase,
                "status": status,
                "summary": summary,
                "created_at": datetime.now(UTC).isoformat(),
            }
            if payload:
                event["payload"] = payload
            try:
                event_callback(event)
            except Exception:
                pass

        # ── Schema Probe ──
        _emit(
            phase="act", status="running", summary="Probing datasource schema, preparing build..."
        )
        probe = self._schema_probe.probe(db=db, requirement_text=goal)
        probe_payload = probe.as_payload()
        datasource_schema = {"tables": probe.columns_by_table} if probe.columns_by_table else None

        # ── Stage 1: Complexity Assessment ──
        _emit(phase="stage_1", status="running", summary="Assessing requirement complexity...")
        stage1 = self._run_analysis_stage(
            stage=1,
            prompt=_STAGE_1_PROMPT_TEMPLATE.format(goal=goal),
            function=function,
            workspace_store=workspace_store,
            datasource_schema=datasource_schema,
            datasource_id=probe.datasource_id,
        )
        stage_results.append(stage1)
        _emit(
            phase="stage_1",
            status="done" if stage1.result_status in ("clear", "refined") else stage1.result_status,
            summary=stage1.assistant_message,
            payload={"result_status": stage1.result_status},
        )

        if stage1.result_status in ("too_complex", "needs_clarification"):
            return StagedBuildResult(
                status=stage1.result_status,
                reason=stage1.result_status,
                final_stage=1,
                stage_results=stage_results,
                schema_probe=probe_payload,
            )

        # ── Stage 2: Requirement Refinement ──
        _emit(phase="stage_2", status="running", summary="Refining requirement specification...")
        stage2 = self._run_analysis_stage(
            stage=2,
            prompt=_STAGE_2_PROMPT_TEMPLATE.format(
                goal=goal,
                stage1_result=stage1.result_text or stage1.assistant_message,
            ),
            function=function,
            workspace_store=workspace_store,
            datasource_schema=datasource_schema,
            datasource_id=probe.datasource_id,
        )
        stage_results.append(stage2)
        _emit(
            phase="stage_2",
            status="done",
            summary=stage2.assistant_message,
            payload={"result_status": stage2.result_status},
        )

        # Refined goal = Stage 2 spec injected into the original goal context
        refined_spec = stage2.result_text or stage2.assistant_message
        effective_goal = self._compose_goal_for_implementation(
            original_goal=goal,
            refined_spec=refined_spec,
            probe_context=probe.goal_context,
        )

        # ── Stage 4+: Implementation via Kernel ──
        workspace_store.set_adapter_event_callback(event_callback)
        runtime = self._runtime_kernel.run(
            scope="function.build",
            initial_goal=effective_goal,
            build_step=lambda goal, attempt_index, attempts: self._builder.build(
                function=function,
                goal=self._compose_goal_for_implementation(
                    original_goal=goal,
                    refined_spec=refined_spec,
                    probe_context=probe.goal_context,
                ),
                workspace_store=workspace_store,
                datasource_schema=datasource_schema,
                datasource_id=probe.datasource_id,
            ),
            verify_step=lambda build_result, goal, attempt_index, attempts: self._verify_attempt(
                function=function,
                db=db,
                schema_probe=probe_payload,
            ),
            summarize_step=lambda build_result: str(
                getattr(build_result, "assistant_message", "")
                or getattr(build_result, "diff_summary", "")
            ).strip(),
            snapshot_step=lambda: str(function.draft_code or ""),
            event_callback=event_callback,
        )

        apply_result = (
            runtime.final_build_result
            if isinstance(runtime.final_build_result, CodingEngineApplyResult)
            else None
        )
        verification_payload = (
            runtime.final_verification.payload if runtime.final_verification is not None else {}
        )
        verification = verification_payload if isinstance(verification_payload, dict) else {}
        if runtime.final_verification is not None and "passed" not in verification:
            verification["passed"] = bool(runtime.final_verification.passed)
        if runtime.final_verification is not None and "diagnostics" not in verification:
            verification["diagnostics"] = list(runtime.final_verification.diagnostics or [])
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

        return StagedBuildResult(
            status=runtime.status,
            reason=runtime.reason,
            final_stage=4 if runtime.status == "done" else 4,
            stage_results=stage_results,
            refined_goal=effective_goal,
            apply_result=apply_result,
            verification=verification,
            attempts=attempts_payload,
            schema_probe=probe_payload,
        )

    def _run_analysis_stage(
        self,
        *,
        stage: int,
        prompt: str,
        function: models.Function,
        workspace_store: WorkspaceStore,
        datasource_schema: dict[str, Any] | None,
        datasource_id: int | None,
    ) -> StagedBuildStageResult:
        """Call the coding engine for analysis — does not update function.draft_code."""
        try:
            result = workspace_store.analyze_function_goal(
                function=function,
                stage_prompt=prompt,
                datasource_schema=datasource_schema,
                datasource_id=datasource_id,
            )
            result_text = str(result.assistant_message or result.diff_summary or "").strip()
            result_status = str(result.result_status or "completed").strip().lower()
            return StagedBuildStageResult(
                stage=stage,
                result_status=result_status,
                assistant_message=result_text or f"Stage {stage} analysis completed",
                result_text=result_text,
            )
        except Exception as exc:
            return StagedBuildStageResult(
                stage=stage,
                result_status="error",
                assistant_message=f"Stage {stage} analysis error: {exc}",
                result_text="",
            )

    def _verify_attempt(
        self,
        *,
        function: models.Function,
        db: Session | None = None,
        schema_probe: dict[str, Any] | None = None,
    ) -> VerificationOutcome:
        verification = self._verifier.verify(function=function, db=db, schema_probe=schema_probe)
        diagnostics = [
            str(item) for item in (verification.get("diagnostics") or []) if str(item).strip()
        ]
        return VerificationOutcome(
            passed=bool(verification.get("passed")),
            diagnostics=diagnostics,
            payload=verification,
            summary="verification_passed"
            if bool(verification.get("passed"))
            else "verification_failed",
        )

    @staticmethod
    def _compose_goal_for_implementation(
        *,
        original_goal: str,
        refined_spec: str,
        probe_context: str,
    ) -> str:
        parts: list[str] = []
        if refined_spec.strip():
            parts.append(
                f"Implementation Specification (from requirements analysis):\n{refined_spec}"
            )
            parts.append(f"\nOriginal Goal:\n{original_goal}")
        else:
            parts.append(original_goal)
        if probe_context.strip():
            parts.append(f"\n{probe_context}")
        return "\n".join(parts)
