from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import threading
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models import models
from app.services.agent.core import summarize_build_goal
from app.services.function.builder import (
    FunctionBuilderService,  # noqa: F401 - kept for test monkeypatch hook
    FunctionBuildRunEvent,
    FunctionBuildRunResult,
)
from app.services.chat.agent import ChatScope, ScopedChatContext
from app.services.chat.scene_agents import SceneAgentPayload, SceneAgentRegistry
from app.services.function.chat_agent import FunctionChatAgent
from app.services.function.authoring_agent import FunctionInvokeCommand
from app.services.function.build_orchestrator import StagedFunctionBuildOrchestrator
from app.services.function.identity import (
    compact_whitespace,
    generate_unique_function_slug,
    normalize_function_display_name,
    normalize_function_slug,
    validate_function_display_name,
    validate_function_slug,
)
from app.services.function.strategy import (
    FunctionStrategyDecider,
    FunctionVerificationHarness,
    StrategyThresholds,
)
from app.services.lifecycle import FunctionLifecycleService, FunctionState, LifecycleValidationError
from app.services.llm import get_llm_client
from app.services.platform.skill_selector import format_skill_context, select_skills_for_context
from app.services.platform.workspace_store import WorkspaceStore

router = APIRouter(prefix="/functions", tags=["Functions"])
logger = get_logger("app.api.functions")
_DEFAULT_FUNCTION_BUILDER_SERVICE_CLASS = FunctionBuilderService
T = TypeVar("T")
_CAPABILITY_PROFILE_VERSION = "function-capability-profile-v1"
_BUSINESS_SUCCESS_HINTS = ("success", "成功", "通过", "返回", "命中", "输出", "ok")
_BUSINESS_FAILURE_HINTS = ("fail", "失败", "异常", "error", "invalid", "缺少", "拒绝", "阻断")


def _serialize(record: Any) -> dict[str, Any]:
    return json.loads(json.dumps(
        {column.name: getattr(record, column.name) for column in record.__table__.columns},
        default=str,
        ensure_ascii=False,
    ))


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _run_async_safely(coro: Awaitable[T]) -> T:
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


def _normalize_scene_string_list(value: Any, *, limit: int = 32) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _resolve_function_scene_agent(
    payload: dict[str, Any],
    *,
    function_id: int,
) -> tuple[FunctionChatAgent | None, SceneAgentPayload | None]:
    raw_scene_agent = payload.get("scene_agent")
    if not isinstance(raw_scene_agent, dict):
        return None, None

    raw_key = str(raw_scene_agent.get("key") or "").strip()
    normalized_key = raw_key or FunctionChatAgent.key
    if normalized_key != FunctionChatAgent.key:
        raise HTTPException(status_code=400, detail=f"scene_agent.key must be {FunctionChatAgent.key}")

    context = raw_scene_agent.get("context") if isinstance(raw_scene_agent.get("context"), dict) else {}
    focus_object = raw_scene_agent.get("focus_object") if isinstance(raw_scene_agent.get("focus_object"), dict) else None

    referenced_ids: list[int] = []
    for raw_value in (
        context.get("function_id"),
        focus_object.get("function_id") if isinstance(focus_object, dict) else None,
    ):
        if isinstance(raw_value, int):
            referenced_ids.append(raw_value)
        elif isinstance(raw_value, str) and raw_value.strip().isdigit():
            referenced_ids.append(int(raw_value.strip()))
    if any(item != function_id for item in referenced_ids):
        raise HTTPException(status_code=400, detail="scene_agent.function_id must match request path")

    normalized_payload = SceneAgentPayload(
        key=normalized_key,
        context=context,
        focus_object=focus_object,
        requested_tools=_normalize_scene_string_list(raw_scene_agent.get("tools")),
        requested_skills=_normalize_scene_string_list(raw_scene_agent.get("skills")),
        source="scene_agent",
    )
    resolved = SceneAgentRegistry().resolve(normalized_payload.key)
    if resolved is not None and not isinstance(resolved, FunctionChatAgent):
        raise HTTPException(status_code=400, detail="scene_agent.key resolved to a non-function agent")
    return (resolved if isinstance(resolved, FunctionChatAgent) else FunctionChatAgent()), normalized_payload


def _merge_function_scene_context(
    *,
    conversation_context: str,
    scene_agent: FunctionChatAgent | None,
    scene_payload: SceneAgentPayload | None,
) -> str:
    base_context = str(conversation_context or "").strip()
    if scene_agent is None or scene_payload is None:
        return base_context
    scene_block = scene_agent.build_prompt_block(scene_payload).strip()
    if not scene_block:
        return base_context
    return "\n\n".join(part for part in (scene_block, base_context) if part)


def _resolve_function_scene_skills(
    *,
    scene_agent: FunctionChatAgent | None,
    scene_payload: SceneAgentPayload | None,
) -> list[str]:
    if scene_agent is None or scene_payload is None:
        return []
    return [item for item in scene_agent.resolve_skills(scene_payload) if item]


def _serialize_function_build_run(run: FunctionBuildRunResult) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "status": run.status,
        "phase": run.phase,
        "summary": run.summary,
        "error_summary": run.error_summary,
        "events": [
            {
                "phase": event.phase,
                "status": event.status,
                "summary": event.summary,
                "created_at": event.created_at,
                "payload": event.payload,
            }
            for event in run.events
        ],
    }


def _serialize_function_build_run_record(
    run: models.FunctionBuildRun,
    *,
    with_events: bool = False,
) -> dict[str, Any]:
    payload = _serialize(run)
    if with_events:
        payload["events"] = [
            _serialize(item)
            for item in sorted(run.events, key=lambda event: event.created_at)
        ]
    return payload


def _serialize_function_chat_events(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in events:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "type": str(item.get("type") or "phase"),
                "phase": item.get("phase"),
                "status": item.get("status"),
                "summary": item.get("summary"),
                "payload": item.get("payload"),
                "created_at": item.get("created_at"),
            }
        )
    return normalized


def _json_dumps_safe(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _emit_function_phase_event(
    event_sink: Callable[[dict[str, Any]], None] | None,
    *,
    phase: str,
    status: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if event_sink is None:
        return
    event: dict[str, Any] = {
        "type": "phase",
        "phase": str(phase or "").strip(),
        "status": str(status or "").strip(),
        "summary": str(summary or "").strip(),
        "created_at": _utc_now_naive().isoformat(),
    }
    if isinstance(payload, dict) and payload:
        event["payload"] = payload
    try:
        event_sink(event)
    except Exception:
        return


_FUNCTION_CORE_PHASES = {"plan", "act", "observe", "reflect", "retry"}


def _should_use_legacy_function_builder() -> bool:
    return FunctionBuilderService is not _DEFAULT_FUNCTION_BUILDER_SERVICE_CLASS


def _run_legacy_function_build(
    *,
    db: Session,
    function: models.Function,
    prompt: str,
) -> tuple[FunctionBuildRunResult, models.FunctionBuildRun]:
    builder = FunctionBuilderService()
    build = builder.apply_prompt(
        current_code=str(function.draft_code or ""),
        current_dependencies=function.draft_dependencies if isinstance(function.draft_dependencies, dict) else {},
        prompt=prompt,
        function_name=str(function.slug or function.name or f"function-{function.id}"),
    )
    summary_text = str(build.summary or "Function draft updated.").strip() or "Function draft updated."
    function.draft_code = build.draft_code
    function.draft_dependencies = build.draft_dependencies if isinstance(build.draft_dependencies, dict) else {}
    function.updated_at = _utc_now_naive()
    build_run = FunctionBuildRunResult(
        run_id=f"fbr_{uuid.uuid4().hex[:16]}",
        status="done",
        phase="apply",
        summary=summary_text,
        draft_code=str(function.draft_code or ""),
        draft_dependencies=function.draft_dependencies if isinstance(function.draft_dependencies, dict) else {},
        events=[
            FunctionBuildRunEvent(
                phase="apply",
                status="done",
                summary=summary_text,
                created_at=_utc_now_naive().isoformat(),
                payload={
                    "diff_summary": summary_text,
                    "assistant_message": summary_text,
                    "legacy_builder": True,
                },
            )
        ],
        error_summary=None,
    )
    db.commit()
    db.refresh(function)
    main_path = WorkspaceStore().sync_function_draft(function)
    function.source_path = str(main_path)
    function.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(function)
    run_record = _persist_function_build_run(
        db,
        function=function,
        action="build",
        prompt=prompt,
        run=build_run,
    )
    db.commit()
    db.refresh(function)
    db.refresh(run_record)
    return build_run, run_record


def _normalize_function_core_phase(phase: str) -> str:
    normalized = str(phase or "").strip().lower()
    if normalized in _FUNCTION_CORE_PHASES:
        return normalized
    if normalized in {"intent_parsed", "clarification", "reuse_recommendation", "suggest_input"}:
        return "plan" if normalized in {"intent_parsed", "clarification", "reuse_recommendation"} else "act"
    if normalized in {"draft_built", "apply", "invoke_started"}:
        return "act"
    if normalized in {"verified", "verify_failed", "failed", "invoke_finished"}:
        return "observe"
    return "act"


def _to_function_stream_phase_event(
    *,
    phase: str,
    status: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    core_phase = _normalize_function_core_phase(phase)
    data_payload = dict(payload) if isinstance(payload, dict) else {}
    raw_phase = str(phase or "").strip()
    if raw_phase and raw_phase != core_phase:
        data_payload["raw_phase"] = raw_phase
    source, agent = _function_event_origin(core_phase)
    data_payload.setdefault("source", source)
    data_payload.setdefault("agent", agent)
    return {
        "type": "phase",
        "event_group": "core",
        "event_name": core_phase,
        "phase": core_phase,
        "data": {
            "status": str(status or "").strip() or "running",
            "summary": str(summary or "").strip(),
            "payload": data_payload,
            "created_at": str(created_at or _utc_now_naive().isoformat()),
        },
    }


def _function_event_origin(core_phase: str) -> tuple[str, str]:
    normalized = str(core_phase or "").strip().lower()
    if normalized == "plan":
        return "llm", "FunctionPlanner"
    if normalized == "act":
        return "runtime", "FunctionBuilderAgent"
    if normalized == "observe":
        return "verifier", "FunctionVerifier"
    if normalized in {"reflect", "retry"}:
        return "llm", "FunctionBuildOrchestrator"
    return "runtime", "FunctionBuildOrchestrator"


def _normalize_business_verification_checks(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _extract_business_verification_checks_from_dependencies(dependencies: dict[str, Any]) -> list[str]:
    business = dependencies.get("business_verification")
    if isinstance(business, dict):
        checks = _normalize_business_verification_checks(business.get("checks"))
        if checks:
            return checks
    builder_spec = dependencies.get("builder_spec")
    if isinstance(builder_spec, dict):
        checks = _normalize_business_verification_checks(builder_spec.get("verification_checks"))
        if checks:
            return checks
    return []


def _derive_default_business_verification_checks(*, prompt: str, function: models.Function) -> list[str]:
    title = str(function.name or "Function").strip() or "Function"
    normalized_prompt = _compact_whitespace(prompt)
    success_case = f"Success path: executing `{title}` returns business fields with `ok=true`."
    failure_case = "Failure path: returns a clear error when required input parameters are missing or invalid."
    if normalized_prompt:
        success_case = f"Success path: returns business-ready data for '{normalized_prompt[:36]}'."
    return [success_case, failure_case]


def _business_verification_checks_cover_success_failure(checks: list[str]) -> bool:
    normalized = [str(item or "").casefold() for item in checks]
    has_success = any(any(token in item for token in _BUSINESS_SUCCESS_HINTS) for item in normalized)
    has_failure = any(any(token in item for token in _BUSINESS_FAILURE_HINTS) for item in normalized)
    return has_success and has_failure


def _normalize_capability_profile(raw: Any) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    db_methods = [str(item).strip() for item in (payload.get("db_methods") or []) if str(item).strip()]
    scheduler_history_calls = [
        str(item).strip()
        for item in (payload.get("scheduler_history_calls") or [])
        if str(item).strip()
    ]
    platform_calls: list[dict[str, Any]] = []
    for item in (payload.get("platform_calls") or []):
        if not isinstance(item, dict):
            continue
        platform_calls.append(
            {
                "method": str(item.get("method") or "").strip() or None,
                "object_type": str(item.get("object_type") or "").strip() or None,
                "action": str(item.get("action") or "").strip() or None,
            }
        )
    input_contract: list[dict[str, Any]] = []
    for item in (payload.get("input_contract") or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        input_contract.append(
            {
                "name": name,
                "type": str(item.get("type") or "string").strip().lower(),
                "required": bool(item.get("required")),
            }
        )
    output_fields: list[dict[str, Any]] = []
    for item in (payload.get("output_fields") or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        output_fields.append(
            {
                "name": name,
                "kind": str(item.get("kind") or "constant").strip(),
            }
        )
    business_verification_checks = _normalize_business_verification_checks(payload.get("business_verification_checks"))
    return {
        "db_methods": sorted(set(db_methods)),
        "platform_calls": sorted(
            platform_calls,
            key=lambda item: (
                str(item.get("method") or ""),
                str(item.get("object_type") or ""),
                str(item.get("action") or ""),
            ),
        ),
        "scheduler_history_calls": sorted(set(scheduler_history_calls)),
        "input_contract": input_contract,
        "output_fields": output_fields,
        "business_verification_checks": business_verification_checks,
    }


def _compute_capability_fingerprint(capability_profile: dict[str, Any]) -> str:
    text = json.dumps(capability_profile, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _derive_contract_from_dependency_manifest(
    dependency_manifest: dict[str, Any] | None,
    *,
    capability_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(dependency_manifest, dict):
        dependency_manifest = {}
    builder_spec = dependency_manifest.get("builder_spec")
    input_contract: list[dict[str, Any]] = []
    output_fields: list[dict[str, Any]] = []
    uses_db = None
    if isinstance(builder_spec, dict):
        uses_db = bool(builder_spec.get("uses_db"))
        for field in (builder_spec.get("input_contract") or []):
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "").strip()
            if not name:
                continue
            input_contract.append(
                {
                    "name": name,
                    "type": str(field.get("type") or "string").strip().lower(),
                    "required": bool(field.get("required")),
                }
            )
        for field in (builder_spec.get("output_fields") or []):
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "").strip()
            if not name:
                continue
            output_fields.append(
                {
                    "name": name,
                    "kind": str(field.get("kind") or "constant").strip(),
                }
            )
    contract: dict[str, Any] = {}
    if uses_db is not None:
        contract["uses_db"] = bool(uses_db)
    if input_contract:
        contract["input_contract"] = input_contract
    if output_fields:
        contract["output_fields"] = output_fields
    profile = _normalize_capability_profile(capability_profile if capability_profile is not None else dependency_manifest.get("capability_profile"))
    if profile:
        contract["capability_profile"] = profile
    return contract


def _apply_verification_governance_to_dependencies(
    *,
    function: models.Function,
    prompt: str,
    dependencies: dict[str, Any] | None,
    verification: dict[str, Any],
    tests_suggested: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    next_dependencies = copy.deepcopy(dependencies) if isinstance(dependencies, dict) else {}
    checks = _extract_business_verification_checks_from_dependencies(next_dependencies)
    if not checks:
        checks = _normalize_business_verification_checks(tests_suggested)
    defaults = _derive_default_business_verification_checks(prompt=prompt, function=function)
    if not checks:
        checks = defaults
    if len(checks) < 2 or not _business_verification_checks_cover_success_failure(checks):
        checks = [*checks, *defaults]
    deduped_checks: list[str] = []
    seen_checks: set[str] = set()
    for item in checks:
        normalized = str(item).strip()
        if not normalized or normalized in seen_checks:
            continue
        seen_checks.add(normalized)
        deduped_checks.append(normalized)
    checks = deduped_checks
    business_verification = {
        "checks": checks,
        "source": "llm_or_default",
        "updated_at": _utc_now_naive().isoformat(),
    }
    next_dependencies["business_verification"] = business_verification
    builder_spec = next_dependencies.get("builder_spec")
    if isinstance(builder_spec, dict):
        builder_spec = copy.deepcopy(builder_spec)
        builder_spec["verification_checks"] = checks
        next_dependencies["builder_spec"] = builder_spec

    capability_profile = _normalize_capability_profile(verification.get("capability_profile"))
    verified_at = str(verification.get("verified_at") or _utc_now_naive().isoformat())
    verification_type = str(verification.get("verification_type") or "pre_release_harness")
    capability_fingerprint = _compute_capability_fingerprint(capability_profile)
    governance = {
        "capability_profile_version": str(verification.get("capability_profile_version") or _CAPABILITY_PROFILE_VERSION),
        "capability_fingerprint": capability_fingerprint,
        "verified_at": verified_at,
        "verification_type": verification_type,
    }
    next_dependencies["capability_profile"] = capability_profile
    next_dependencies["capability_profile_version"] = governance["capability_profile_version"]
    next_dependencies["governance"] = governance
    return next_dependencies, {"business_verification": business_verification, **governance}


def _load_recent_build_contexts(db: Session, *, function_id: int, limit: int = 6) -> list[dict[str, str]]:
    rows = (
        db.query(models.FunctionBuildRun)
        .filter(models.FunctionBuildRun.function_id == function_id, models.FunctionBuildRun.action == "build")
        .order_by(models.FunctionBuildRun.created_at.desc())
        .limit(max(1, min(int(limit or 1), 20)))
        .all()
    )
    return [
        {
            "prompt": str(row.prompt or "").strip(),
            "status": str(row.status or "").strip(),
            "error": str(row.error_summary or "").strip(),
            "summary": str(row.result_summary or "").strip(),
        }
        for row in rows
    ]


def _compose_function_engine_goal(
    user_prompt: str,
    *,
    latest_context: dict[str, str] | None = None,
    recent_contexts: list[dict[str, str]] | None = None,
    conversation_context: str = "",
) -> str:
    history: list[dict[str, str]] = []
    if recent_contexts:
        history = [item for item in recent_contexts if isinstance(item, dict)]
    elif latest_context:
        history = [latest_context]
    chat_agent = FunctionChatAgent()
    return chat_agent.compose_function_build_goal(
        prompt=str(user_prompt or ""),
        recent_contexts=history,
        conversation_context=conversation_context,
    )


_FUNCTION_BUILD_INTERNAL_DETAIL_TOKENS = (
    "get_session_by_id",
    "sqlalchemy",
    "mappings()",
    "text(",
    "row[",
    "database key",
    "coding engine",
    "functionbase",
    "positional index",
    "dict key",
)

_FUNCTION_DESCRIPTION_PLACEHOLDER_TOKENS = (
    "created from function console",
    "verify business results in the test panel",
    "please continue verifying results",
)
_TEMPORARY_FUNCTION_NAME_PREFIX = "Untitled Function"
_FUNCTION_DESCRIPTION_NOISE_TOKENS = (
    "test",
    "verify",
    "build",
    "fix",
    "error",
    "failed",
    "diff",
    "main.py",
)


def _contains_text_ci(text: str, fragment: str) -> bool:
    base = str(text or "")
    target = str(fragment or "")
    if not target:
        return False
    return target.casefold() in base.casefold()


def _contains_any_text_ci(text: str, fragments: tuple[str, ...]) -> bool:
    return any(_contains_text_ci(text, token) for token in fragments if str(token or ""))


def _validate_function_display_name_or_raise(name: str) -> None:
    detail = validate_function_display_name(name)
    if detail is not None:
        raise HTTPException(status_code=400, detail=detail)


def _validate_function_slug_or_raise(slug: str) -> None:
    if not slug:
        raise HTTPException(status_code=400, detail="slug is required")
    if not validate_function_slug(slug):
        raise HTTPException(
            status_code=400,
            detail="slug must match ^[a-z][a-z0-9_-]{2,63}$",
        )


def _function_slug_exists(
    db: Session,
    *,
    slug: str,
    exclude_function_id: int | None = None,
) -> bool:
    query = db.query(models.Function).filter(models.Function.slug == slug)
    if exclude_function_id is not None:
        query = query.filter(models.Function.id != exclude_function_id)
    return query.first() is not None


def _generate_function_slug(
    db: Session,
    *,
    display_name: str,
    exclude_function_id: int | None = None,
) -> str:
    return generate_unique_function_slug(
        display_name,
        exists=lambda candidate: _function_slug_exists(
            db,
            slug=candidate,
            exclude_function_id=exclude_function_id,
        ),
    )


def _ensure_function_slug_available(
    db: Session,
    *,
    slug: str,
    exclude_function_id: int | None = None,
) -> None:
    if _function_slug_exists(db, slug=slug, exclude_function_id=exclude_function_id):
        raise HTTPException(status_code=409, detail=f"function slug already exists: {slug}")


def _validate_slug_mutation_absent(payload: dict[str, Any]) -> None:
    if "slug" in payload:
        raise HTTPException(status_code=400, detail="slug is managed by the system")


def _compact_whitespace(text: str) -> str:
    return compact_whitespace(text)


def _validate_function_name_or_raise(name: str) -> None:
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    _validate_function_display_name_or_raise(name)


def _generate_temporary_function_name() -> str:
    return f"{_TEMPORARY_FUNCTION_NAME_PREFIX} {uuid.uuid4().hex[:6]}"


def _is_temporary_function_name(name: str) -> bool:
    normalized = _compact_whitespace(name)
    return bool(normalized) and normalized.startswith(_TEMPORARY_FUNCTION_NAME_PREFIX)


def _coerce_generated_function_name(candidate: str) -> str:
    normalized = normalize_function_display_name(candidate)
    if not normalized or _is_temporary_function_name(normalized):
        normalized = ""
    if not normalized:
        return ""
    detail = validate_function_display_name(normalized)
    if detail is not None:
        if len(normalized) > 255:
            normalized = normalized[:255].rstrip()
        detail = validate_function_display_name(normalized)
    return normalized if detail is None else ""


def _coerce_generated_function_description(candidate: str, *, function: models.Function, prompt: str) -> str:
    normalized = _compact_whitespace(candidate)
    if normalized:
        return normalized[:500].rstrip()
    return _derive_function_semantic_description(function=function, prompt=prompt)


def _derive_function_semantic_title(*, prompt: str, build_summary: str, function: models.Function) -> str:
    candidates = [
        _compact_whitespace(build_summary).rstrip("。.!?，,；;：:"),
        _compact_whitespace(prompt).rstrip("。.!?，,；;：:"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if _contains_any_text_ci(candidate, _FUNCTION_DESCRIPTION_NOISE_TOKENS):
            continue
        return candidate[:40].rstrip()
    return str(function.name or "")


def _parse_llm_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _generate_initial_function_metadata_via_llm(*, prompt: str, build_summary: str) -> tuple[str, str]:
    system_prompt = (
        "You are responsible for generating one-time user-visible metadata for a newly created Function.\n"
        "Return JSON with exactly two fields: title, description.\n"
        "Requirements:\n"
        "1) title is a short title suitable as the user-visible Function name; do not include temporary/draft/untitled/test results.\n"
        "2) description is a concise one-sentence functional description; do not include test results, build process, or implementation details.\n"
        "3) Directly describe what the Function does.\n"
        "4) Do not output markdown or add other fields."
    )
    user_prompt = (
        f"User's initial request: {_compact_whitespace(prompt)}\n"
        f"Build summary: {_compact_whitespace(build_summary)}"
    )

    async def _call() -> tuple[str, str]:
        client = get_llm_client()
        response: dict[str, Any] | None = None
        async for chunk in client.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=None,
            stream=False,
            temperature=0.0,
            response_format={"type": "json_object"},
        ):
            response = chunk
            break
        if response is None:
            return "", ""
        content = (((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        payload = _parse_llm_json_object(content)
        return (
            _compact_whitespace(str(payload.get("title") or "")),
            _compact_whitespace(str(payload.get("description") or "")),
        )

    try:
        result = _run_async_safely(_call())
    except Exception:
        return "", ""
    if not isinstance(result, tuple) or len(result) != 2:
        return "", ""
    return str(result[0] or ""), str(result[1] or "")


def _apply_initial_function_metadata(
    *,
    function: models.Function,
    prompt: str,
    build_summary: str,
    generated_title: str,
    generated_description: str,
) -> None:
    resolved_title = _compact_whitespace(generated_title)
    resolved_description = _compact_whitespace(generated_description)
    if not resolved_title or not resolved_description:
        llm_title, llm_description = _generate_initial_function_metadata_via_llm(
            prompt=prompt,
            build_summary=build_summary,
        )
        if not resolved_title:
            resolved_title = llm_title
        if not resolved_description:
            resolved_description = llm_description
    if _is_temporary_function_name(str(function.name or "")):
        next_name = _coerce_generated_function_name(resolved_title)
        if not next_name:
            next_name = _derive_function_semantic_title(
                prompt=prompt,
                build_summary=build_summary,
                function=function,
            )
        if next_name:
            function.name = next_name
    if _is_placeholder_function_description(str(function.description or "")):
        function.description = _coerce_generated_function_description(
            resolved_description,
            function=function,
            prompt=prompt,
        )


def _extract_builder_semantic_summary(function: models.Function) -> str:
    deps = function.draft_dependencies if isinstance(function.draft_dependencies, dict) else {}
    builder_spec = deps.get("builder_spec") if isinstance(deps.get("builder_spec"), dict) else {}
    meta = builder_spec.get("meta") if isinstance(builder_spec.get("meta"), dict) else {}
    candidates = [
        str(meta.get("summary") or "").strip(),
        str(builder_spec.get("summary") or "").strip(),
    ]
    for candidate in candidates:
        if candidate and not _contains_any_text_ci(candidate, _FUNCTION_DESCRIPTION_NOISE_TOKENS):
            return candidate
    return ""


def _is_placeholder_function_description(description: str) -> bool:
    normalized = str(description or "").strip()
    if not normalized:
        return True
    return _contains_any_text_ci(normalized, _FUNCTION_DESCRIPTION_PLACEHOLDER_TOKENS)


def _derive_function_semantic_description(*, function: models.Function, prompt: str) -> str:
    from_spec = _extract_builder_semantic_summary(function)
    if from_spec:
        return from_spec
    normalized_prompt = _compact_whitespace(prompt)
    if not normalized_prompt:
        return f"Business logic implementation of Function `{function.name}`."
    normalized_prompt = re.sub(r"^(请|帮我|麻烦|把|将|需要|我要|想要)\s*", "", normalized_prompt)
    normalized_prompt = re.sub(r"[。.!?]+$", "", normalized_prompt)
    if len(normalized_prompt) > 96:
        normalized_prompt = normalized_prompt[:96].rstrip() + "..."
    return f"For {normalized_prompt}."


def _normalize_function_build_summary(*, assistant_message: str, diff_summary: str) -> str:
    message = str(assistant_message or "").strip()
    if message and not _contains_any_text_ci(message, _FUNCTION_BUILD_INTERNAL_DETAIL_TOKENS):
        return message
    if str(diff_summary or "").strip():
        return "Function draft updated. Please verify business results in the test panel."
    return "Function draft updated. Please continue verifying results."


def _compose_no_change_build_summary() -> str:
    return (
        "No changes detected to `main.py` in this Function build; draft code remains unchanged. "
        "Please provide more specific revision requirements and try again."
    )


def _classify_lifecycle_error_code(message: str) -> str:
    detail = str(message or "").strip()
    if _contains_text_ci(detail, "no released version for production path") or _contains_text_ci(detail, "release must be persisted"):
        return "release_required"
    if _contains_text_ci(detail, "runtime_path must be production or draft"):
        return "invalid_runtime_path"
    return "validation_error"


def _raise_lifecycle_http_error(err: LifecycleValidationError) -> None:
    detail = str(err)
    raise HTTPException(
        status_code=400,
        detail={
            "error_code": _classify_lifecycle_error_code(detail),
            "message": detail,
        },
    ) from err


def _friendly_invoke_error_message(message: str, *, error_code: str | None = None) -> str:
    raw = _compact_whitespace(message)
    code = str(error_code or "").strip()
    if code == "release_required" or _contains_text_ci(raw, "no released version for production path"):
        return "The current draft has no released version. Please release first before production execution."
    if (
        _contains_text_ci(raw, "scheduler_history.delete 使用 dry_run=true")
        or _contains_text_ci(raw, "dry_run=true")
        or _contains_text_ci(raw, "dry_run=True")
    ):
        return "The current test execution only supports dry-run. Keep dry_run=true when cleaning history; for actual cleanup, use production execution after release or delegate to Scheduler."
    if _contains_text_ci(raw, "plan 模式禁止控制面写操作") or _contains_text_ci(raw, "请先确认后使用 apply 模式执行"):
        return "The current test execution only supports dry-run and cannot directly modify platform objects. After release, changes can take effect through production execution or Scheduler."
    if _contains_text_ci(raw, "plan 模式禁止控制面 operate 操作"):
        return "The current test execution only supports dry-run and cannot directly perform platform operations. After release, operations can take effect through production execution or Scheduler."
    if _contains_text_ci(raw, "confirm_apply"):
        return "Explicit confirmation is required before production execution."
    return raw or "Test execution failed."


def _friendly_invoke_success_message(output: Any) -> str:
    if isinstance(output, dict):
        object_type = str(output.get("object_type") or "").strip()
        action = str(output.get("action") or "").strip()
        if _contains_text_ci(object_type, "scheduler_history") and _contains_text_ci(action, "delete"):
            candidate_count = int(output.get("candidate_count") or 0)
            deleted_count = int(output.get("deleted_count") or 0)
            if bool(output.get("dry_run")):
                return f"History cleanup dry-run completed, matched {candidate_count} records."
            return f"History cleanup completed, {deleted_count} records removed."
        if any(key in output for key in ("execution_mode", "runtime_path", "write_mode", "confirm_apply")):
            return "Test execution completed. You can view the results in the right panel."
    raw = _compact_whitespace(str(output or ""))
    if _contains_any_text_ci(raw, ("execution_mode", "runtime_path", "write_mode", "confirm_apply")):
        return "Test execution completed. You can view the results in the right panel."
    if raw and len(raw) <= 120 and not raw.startswith("{"):
        return raw
    return "Test execution succeeded. You can view the results in the right panel."


def _build_function_apply_failed_run(
    *,
    function: models.Function,
    error_message: str,
) -> FunctionBuildRunResult:
    summary = f"Function build failed: {error_message}"
    return FunctionBuildRunResult(
        run_id=f"fbr_{uuid.uuid4().hex[:16]}",
        status="failed",
        phase="apply",
        summary=summary,
        draft_code=str(function.draft_code or ""),
        draft_dependencies=function.draft_dependencies if isinstance(function.draft_dependencies, dict) else {},
        events=[
            FunctionBuildRunEvent(
                phase="apply",
                status="failed",
                summary=summary,
                created_at=_utc_now_naive().isoformat(),
                payload={"engine": "pi_lite", "error": error_message},
            )
        ],
        error_summary=error_message,
    )


def _build_function_verify_failed_run(
    *,
    function: models.Function,
    verification: dict[str, Any],
    changed_files: list[str],
) -> FunctionBuildRunResult:
    diagnostics = [str(item) for item in (verification.get("diagnostics") or []) if str(item).strip()]
    checks = [item for item in (verification.get("checks") or []) if isinstance(item, dict)]
    failed_check_names = {
        str(item.get("name") or "")
        for item in checks
        if bool(item.get("passed")) is False
    }
    mapping_row_failed = (
        "mapping_row_access_valid" in failed_check_names
        or "mapping_row_access_detail" in failed_check_names
    )
    if mapping_row_failed and not any("db.query" in item for item in diagnostics):
        diagnostics.append("Process rows returned by db.query/query_by_id using row.get(...) key access for dict-style fields.")
    brief = "; ".join(diagnostics[:3]) if diagnostics else "Draft did not pass runtime contract verification"
    summary = f"Function build failed contract verification: {brief}"
    return FunctionBuildRunResult(
        run_id=f"fbr_{uuid.uuid4().hex[:16]}",
        status="failed",
        phase="verify_failed",
        summary=summary,
        draft_code=str(function.draft_code or ""),
        draft_dependencies=function.draft_dependencies if isinstance(function.draft_dependencies, dict) else {},
        events=[
            FunctionBuildRunEvent(
                phase="verify_failed",
                status="failed",
                summary=summary,
                created_at=_utc_now_naive().isoformat(),
                payload={
                    "engine": "pi_lite",
                    "changed_files": changed_files,
                    "verification": verification,
                },
            )
        ],
        error_summary=summary,
    )


def _persist_function_build_run(
    db: Session,
    *,
    function: models.Function,
    action: str,
    prompt: str,
    run: FunctionBuildRunResult,
) -> models.FunctionBuildRun:
    run_record = models.FunctionBuildRun(
        run_id=run.run_id,
        function_id=function.id,
        action=action,
        status=run.status,
        phase=run.phase,
        prompt=prompt,
        result_summary=run.summary,
        error_summary=run.error_summary,
        started_at=_utc_now_naive(),
        finished_at=_utc_now_naive(),
    )
    db.add(run_record)
    db.flush()
    for item in run.events:
        db.add(
            models.FunctionBuildEvent(
                build_run_id=run_record.id,
                phase=item.phase,
                status=item.status,
                summary=item.summary,
                payload=item.payload if isinstance(item.payload, dict) else None,
                created_at=datetime.fromisoformat(item.created_at)
                if isinstance(item.created_at, str)
                else _utc_now_naive(),
            )
        )
    db.flush()
    db.refresh(run_record)
    return run_record


def _should_short_circuit_build_for_reuse(
    *,
    function: models.Function,
    previous_draft_code: str,
    previous_draft_dependencies: Any,
    decision: dict[str, Any] | None,
) -> bool:
    if function.status != FunctionState.DRAFT.value:
        return False
    if str(previous_draft_code or "").strip():
        return False
    if isinstance(previous_draft_dependencies, dict) and previous_draft_dependencies:
        return False
    if not isinstance(decision, dict):
        return False
    if str(decision.get("strategy") or "") != "reuse":
        return False
    top_candidate = decision.get("top_candidate")
    return isinstance(top_candidate, dict) and int(top_candidate.get("function_id") or 0) > 0


def _compose_reuse_recommendation_summary(decision: dict[str, Any]) -> str:
    top_candidate = decision.get("top_candidate") if isinstance(decision.get("top_candidate"), dict) else {}
    candidate_name = str(top_candidate.get("name") or "").strip() or f"Function#{int(top_candidate.get('function_id') or 0)}"
    score = float(top_candidate.get("score") or 0.0)
    return f"Detected a highly matching reusable capability `{candidate_name}` (score={score:.2f}); recommend reusing to avoid fragmentation."


def _restore_function_workspace_snapshot(
    *,
    workspace: WorkspaceStore,
    function: models.Function,
    draft_code: str,
    draft_dependencies: Any,
) -> None:
    function.draft_code = draft_code
    function.draft_dependencies = draft_dependencies
    try:
        main_path = workspace.sync_function_draft(function)
        function.source_path = str(main_path)
    except Exception as exc:
        logger.warning(
            "function_workspace_restore_failed %s",
            fmt_kv(function_id=function.id, error=str(exc)),
        )


def _run_function_build_action(
    *,
    db: Session,
    function: models.Function,
    prompt: str,
    conversation_context: str = "",
    skill_context: str = "",
    action: str = "build",
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[FunctionBuildRunResult, models.FunctionBuildRun]:
    logger.info(
        "function_build_mode_selected %s",
        fmt_kv(function_id=function.id, action=action, engine="pi_lite"),
    )
    workspace = WorkspaceStore()
    orchestrator = StagedFunctionBuildOrchestrator()
    build_applied = False
    previous_draft_code = str(function.draft_code or "")
    previous_draft_dependencies = (
        copy.deepcopy(function.draft_dependencies) if isinstance(function.draft_dependencies, dict) else function.draft_dependencies
    )
    requirement_contract = _derive_contract_from_dependency_manifest(
        previous_draft_dependencies if isinstance(previous_draft_dependencies, dict) else {},
    )
    _emit_function_phase_event(
        event_sink,
        phase="plan",
        status="running",
        summary="Analyzing requirements and formulating build strategy...",
        payload={"source": "llm", "agent": "FunctionPlanner"},
    )
    plan_started_at = perf_counter()
    recent_contexts = _load_recent_build_contexts(db, function_id=function.id)
    plan_result = orchestrator.plan(
        db=db,
        function=function,
        prompt=prompt,
        conversation_context=conversation_context,
        skill_context=skill_context,
        recent_contexts=recent_contexts,
        requirement_contract=requirement_contract if requirement_contract else None,
    )
    strategy_decision = plan_result.strategy_decision
    plan_elapsed_ms = max(1, int((perf_counter() - plan_started_at) * 1000))
    _goal_text = str(plan_result.goal or "").strip()
    _goal_preview = summarize_build_goal(_goal_text, max_len=200) if _goal_text else ""
    _emit_function_phase_event(
        event_sink,
        phase="plan",
        status="done",
        summary=_goal_preview or "Requirement planning complete, entering build phase",
        payload=(
            {
                "strategy": str(strategy_decision.get("strategy") or ""),
                "phase_duration_ms": plan_elapsed_ms,
                "source": "llm",
                "agent": "FunctionPlanner",
            }
            if isinstance(strategy_decision, dict)
            else {"phase_duration_ms": plan_elapsed_ms, "source": "llm", "agent": "FunctionPlanner"}
        ),
    )
    if _should_short_circuit_build_for_reuse(
        function=function,
        previous_draft_code=previous_draft_code,
        previous_draft_dependencies=previous_draft_dependencies,
        decision=strategy_decision,
    ):
        summary = _compose_reuse_recommendation_summary(strategy_decision)
        build_run = FunctionBuildRunResult(
            run_id=f"fbr_{uuid.uuid4().hex[:16]}",
            status="done",
            phase="reuse_recommendation",
            summary=summary,
            draft_code=previous_draft_code,
            draft_dependencies=previous_draft_dependencies if isinstance(previous_draft_dependencies, dict) else {},
            events=[
                FunctionBuildRunEvent(
                    phase="reuse_recommendation",
                    status="done",
                    summary=summary,
                    created_at=_utc_now_naive().isoformat(),
                    payload={"strategy_decision": strategy_decision},
                )
            ],
            error_summary=None,
        )
        run_record = _persist_function_build_run(
            db,
            function=function,
            action=action,
            prompt=prompt,
            run=build_run,
        )
        db.commit()
        db.refresh(function)
        db.refresh(run_record)
        return build_run, run_record
    try:
        orchestrated = orchestrator.execute(
            db=db,
            function=function,
            goal=plan_result.goal,
            workspace_store=workspace,
            strategy_decision=strategy_decision,
            event_callback=event_sink,
        )

        # Stage 1 or 2 returned needs_clarification / too_complex — surface to user,
        # do NOT restore workspace (nothing was written), do NOT treat as build failure.
        # Only triggers for early-stage exits (final_stage 1 or 2); Stage 4 Kernel failures
        # fall through to the normal verification/failed path below.
        if orchestrated.status in ("needs_clarification", "too_complex") and getattr(orchestrated, "final_stage", 4) in (1, 2):
            stage_results_list = getattr(orchestrated, "stage_results", [])
            last_stage_msg = next(
                (sr.assistant_message for sr in reversed(stage_results_list) if sr.assistant_message),
                "Additional information is needed to complete the build",
            )
            clarification_run = FunctionBuildRunResult(
                run_id=f"fbr_{uuid.uuid4().hex[:16]}",
                status=orchestrated.status,
                phase="stage_assessment",
                summary=last_stage_msg,
                draft_code=previous_draft_code,
                draft_dependencies=(
                    previous_draft_dependencies
                    if isinstance(previous_draft_dependencies, dict)
                    else {}
                ),
                events=[
                    FunctionBuildRunEvent(
                        phase="stage_assessment",
                        status=orchestrated.status,
                        summary=last_stage_msg,
                        created_at=_utc_now_naive().isoformat(),
                        payload={
                            "orchestrator_status": orchestrated.status,
                            "final_stage": getattr(orchestrated, "final_stage", 1),
                        },
                    )
                ],
                error_summary=None,
            )
            run_record = _persist_function_build_run(
                db,
                function=function,
                action=action,
                prompt=prompt,
                run=clarification_run,
            )
            db.commit()
            db.refresh(function)
            db.refresh(run_record)
            return clarification_run, run_record

        result = orchestrated.apply_result
        changed_files = [str(item) for item in ((result.changed_files if result is not None else []) or []) if str(item)]
        current_draft_code = str(function.draft_code or "")
        build_applied = ("main.py" in changed_files) or (current_draft_code != previous_draft_code)
        verification = orchestrated.verification
        if not bool(verification.get("passed")):
            _restore_function_workspace_snapshot(
                workspace=workspace,
                function=function,
                draft_code=previous_draft_code,
                draft_dependencies=previous_draft_dependencies,
            )
            build_run = _build_function_verify_failed_run(
                function=function,
                verification=verification,
                changed_files=changed_files,
            )
            if build_run.events and isinstance(build_run.events[0].payload, dict):
                build_run.events[0].payload["attempts"] = orchestrated.attempts
                build_run.events[0].payload["schema_probe"] = orchestrated.schema_probe
                build_run.events[0].payload["orchestrator_status"] = orchestrated.status
                build_run.events[0].payload["orchestrator_reason"] = orchestrated.reason
            run_record = _persist_function_build_run(
                db,
                function=function,
                action=action,
                prompt=prompt,
                run=build_run,
            )
            db.commit()
            db.refresh(function)
            db.refresh(run_record)
            return build_run, run_record
        if result is None:
            raise ValueError("function build orchestrator returned no apply result")
        governed_dependencies, governance_snapshot = _apply_verification_governance_to_dependencies(
            function=function,
            prompt=prompt,
            dependencies=function.draft_dependencies if isinstance(function.draft_dependencies, dict) else {},
            verification=verification,
            tests_suggested=result.tests_suggested,
        )
        function.draft_dependencies = governed_dependencies
        if build_applied:
            build_summary = _normalize_function_build_summary(
                assistant_message=str(result.assistant_message or ""),
                diff_summary=str(result.diff_summary or ""),
            )
        else:
            build_summary = _compose_no_change_build_summary()
        build_run = FunctionBuildRunResult(
            run_id=f"fbr_{uuid.uuid4().hex[:16]}",
            status="done",
            phase="apply",
            summary=build_summary or "Function draft updated.",
            draft_code=str(function.draft_code or ""),
            draft_dependencies=function.draft_dependencies if isinstance(function.draft_dependencies, dict) else {},
            events=[
                FunctionBuildRunEvent(
                    phase="apply",
                    status="done",
                    summary=build_summary or "Function draft updated.",
                    created_at=_utc_now_naive().isoformat(),
                    payload={
                        "changed_files": changed_files,
                        "tests_suggested": result.tests_suggested,
                        "risk_notes": result.risk_notes,
                        "no_changes_applied": not build_applied,
                        "engine": "pi_lite",
                        "strategy_decision": strategy_decision,
                        "attempts": orchestrated.attempts,
                        "schema_probe": orchestrated.schema_probe,
                        "orchestrator_status": orchestrated.status,
                        "orchestrator_reason": orchestrated.reason,
                        "verification": {
                            "passed": bool(verification.get("passed")),
                            "diagnostic_count": len(verification.get("diagnostics") or []),
                        },
                        "governance": governance_snapshot,
                    },
                )
            ],
            error_summary=None,
        )
    except Exception as err:
        _restore_function_workspace_snapshot(
            workspace=workspace,
            function=function,
            draft_code=previous_draft_code,
            draft_dependencies=previous_draft_dependencies,
        )
        build_run = _build_function_apply_failed_run(
            function=function,
            error_message=str(err),
        )
        _emit_function_phase_event(
            event_sink,
            phase="observe",
            status="failed",
            summary=f"Observe: build process error: {str(err)}",
        )

    if build_run.status == "done":
        function.draft_code = build_run.draft_code
        function.draft_dependencies = build_run.draft_dependencies
        metadata_before = (function.name, function.description)
        if build_applied:
            _apply_initial_function_metadata(
                function=function,
                prompt=prompt,
                build_summary=str(build_run.summary or ""),
                generated_title=str(result.generated_title or "") if "result" in locals() else "",
                generated_description=str(result.generated_description or "") if "result" in locals() else "",
            )
        draft_iteration_started = build_applied or metadata_before != (function.name, function.description)
        if draft_iteration_started and function.status == FunctionState.RELEASED.value:
            FunctionLifecycleService().transition(function, target_state=FunctionState.DRAFT)
        function.updated_at = _utc_now_naive()
        db.commit()
        db.refresh(function)
        main_path = workspace.sync_function_draft(function)
        function.source_path = str(main_path)
        function.updated_at = _utc_now_naive()
        db.commit()
        db.refresh(function)

    run_record = _persist_function_build_run(
        db,
        function=function,
        action=action,
        prompt=prompt,
        run=build_run,
    )
    db.commit()
    db.refresh(function)
    db.refresh(run_record)
    return build_run, run_record


def _get_function_or_404(db: Session, function_id: int) -> models.Function:
    function = db.query(models.Function).filter(models.Function.id == function_id).first()
    if function is None:
        raise HTTPException(status_code=404, detail=f"Function {function_id} not found")
    return function


def _guard_not_builtin(function: models.Function, *, action: str) -> None:
    if getattr(function, "kind", "custom") == "built_in":
        raise HTTPException(
            status_code=403,
            detail=f"Built-in function cannot be {action}. Duplicate it to create an editable copy.",
        )


@router.get("")
def list_functions(db: Session = Depends(get_db)):
    records = db.query(models.Function).order_by(models.Function.updated_at.desc()).all()
    return [_serialize(item) for item in records]


@router.get("/runs")
def list_all_function_runs(limit: int = 50, db: Session = Depends(get_db)):
    normalized_limit = max(1, min(limit, 200))
    runs = (
        db.query(models.FunctionRun)
        .order_by(models.FunctionRun.created_at.desc())
        .limit(normalized_limit)
        .all()
    )
    result = []
    for run in runs:
        row = _serialize(run)
        row["function_name"] = run.function.name if run.function else None
        row["function_slug"] = run.function.slug if run.function else None
        result.append(row)
    return result


@router.post("", status_code=status.HTTP_201_CREATED)
def create_function(payload: dict[str, Any], db: Session = Depends(get_db)):
    _validate_slug_mutation_absent(payload)
    name = normalize_function_display_name(payload.get("name"))
    if not name:
        name = _generate_temporary_function_name()
    _validate_function_display_name_or_raise(name)
    slug = _generate_function_slug(db, display_name=name)
    function = models.Function(
        name=name,
        slug=slug,
        description=_compact_whitespace(str(payload.get("description") or "")) or None,
        kind="custom",
        status="draft",
        draft_code=payload.get("draft_code"),
        draft_dependencies=payload.get("draft_dependencies"),
    )
    db.add(function)
    db.commit()
    db.refresh(function)
    main_path = WorkspaceStore().sync_function_draft(function)
    function.source_path = str(main_path)
    function.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(function)
    return _serialize(function)


@router.get("/{function_id}")
def get_function(function_id: int, db: Session = Depends(get_db)):
    return _serialize(_get_function_or_404(db, function_id))



@router.get("/by-slug/{function_slug}")
def get_function_by_slug(function_slug: str, db: Session = Depends(get_db)):
    normalized_slug = normalize_function_slug(function_slug)
    _validate_function_slug_or_raise(normalized_slug)
    record = db.query(models.Function).filter(models.Function.slug == normalized_slug).first()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Function {normalized_slug} not found")
    return _serialize(record)


@router.get("/by-name/{function_name}")
def get_function_by_name(function_name: str, db: Session = Depends(get_db)):
    normalized_slug = normalize_function_slug(function_name)
    _validate_function_slug_or_raise(normalized_slug)
    record = db.query(models.Function).filter(models.Function.slug == normalized_slug).first()
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Function slug {normalized_slug} not found; display name is not a stable lookup key",
        )
    return _serialize(record)


@router.patch("/{function_id}")
def update_function(function_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    function = _get_function_or_404(db, function_id)
    _validate_slug_mutation_absent(payload)
    if any(field in payload for field in ("draft_code", "draft_dependencies")):
        _guard_not_builtin(function, action="edited")
    mutated = False
    if "name" in payload:
        next_name = normalize_function_display_name(payload.get("name"))
        _validate_function_display_name_or_raise(next_name)
        if function.name != next_name:
            function.name = next_name
            mutated = True
    if "description" in payload:
        next_description = _compact_whitespace(str(payload.get("description") or "")) or None
        if function.description != next_description:
            function.description = next_description
            mutated = True
    for field in ("draft_code", "draft_dependencies"):
        if field in payload:
            if getattr(function, field) != payload[field]:
                setattr(function, field, payload[field])
                mutated = True
    if mutated and function.status == FunctionState.RELEASED.value:
        FunctionLifecycleService().transition(function, target_state=FunctionState.DRAFT)
    function.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(function)
    main_path = WorkspaceStore().sync_function_draft(function)
    function.source_path = str(main_path)
    function.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(function)
    return _serialize(function)


@router.post("/{function_id}/build")
def build_function(function_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    function = _get_function_or_404(db, function_id)
    _guard_not_builtin(function, action="rebuilt")
    prompt = str(payload.get("prompt") or "").strip()
    conversation_context = str(payload.get("conversation_context") or "")
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    if _should_use_legacy_function_builder():
        build_run, run_record = _run_legacy_function_build(
            db=db,
            function=function,
            prompt=prompt,
        )
    else:
        build_run, run_record = _run_function_build_action(
            db=db,
            function=function,
            prompt=prompt,
            conversation_context=conversation_context,
            action="build",
        )
    return {
        "function": _serialize(function),
        "build_summary": build_run.summary,
        "build_run": _serialize_function_build_run_record(run_record, with_events=True),
    }


@router.post("/{function_id}/chat")
async def run_function_chat_action(function_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    function = _get_function_or_404(db, function_id)
    action = str(payload.get("action") or "build").strip().lower()
    context = ScopedChatContext(scope=ChatScope.FUNCTION_BUILD)
    chat_agent = FunctionChatAgent()
    scene_agent, scene_payload = _resolve_function_scene_agent(payload, function_id=function_id)

    if action == "build":
        _guard_not_builtin(function, action="rebuilt")
        prompt = str(payload.get("prompt") or "").strip()
        conversation_context = _merge_function_scene_context(
            conversation_context=str(payload.get("conversation_context") or ""),
            scene_agent=scene_agent,
            scene_payload=scene_payload,
        )
        scene_skill_context = format_skill_context(
            _resolve_function_scene_skills(scene_agent=scene_agent, scene_payload=scene_payload)
        )
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt is required")
        build_run, run_record = _run_function_build_action(
            db=db,
            function=function,
            prompt=prompt,
            conversation_context=conversation_context,
            skill_context=scene_skill_context,
            action=action,
        )
        assistant_message = str(build_run.summary or "").strip()
        return {
            "scope": "function.build",
            "action": action,
            "status": build_run.status,
            "assistant_message": assistant_message,
            "events": _serialize_function_chat_events(
                [
                    {
                        "type": "phase",
                        "phase": item.phase,
                        "status": item.status,
                        "summary": item.summary,
                        "payload": item.payload,
                        "created_at": item.created_at,
                    }
                    for item in build_run.events
                ]
            ),
            "data": {
                "function": _serialize(function),
                "build_run": _serialize_function_build_run_record(run_record, with_events=True),
            },
        }

    if action == "suggest_input":
        prompt = str(payload.get("prompt") or "").strip() or "Generate directly runnable test input based on the current Function."
        conversation_context = str(payload.get("conversation_context") or "")
        suggestion = chat_agent.suggest_function_input(
            function=function,
            prompt=prompt,
            conversation_context=conversation_context,
            context=context,
        )
        rationale = str(suggestion.get("rationale") or "Test input suggestion generated.").strip()
        suggest_run = FunctionBuildRunResult(
            run_id=f"fbr_{uuid.uuid4().hex[:16]}",
            status="done",
            phase="suggest_input",
            summary=rationale or "Test input suggestion generated.",
            draft_code=str(function.draft_code or ""),
            draft_dependencies=function.draft_dependencies if isinstance(function.draft_dependencies, dict) else {},
            events=[
                FunctionBuildRunEvent(
                    phase="suggest_input",
                    status="done",
                    summary="Test input suggestion generated.",
                    created_at=_utc_now_naive().isoformat(),
                    payload={
                        "missing_information": suggestion.get("missing_information") or [],
                        "assumptions": suggestion.get("assumptions") or [],
                    },
                )
            ],
            error_summary=None,
        )
        run_record = _persist_function_build_run(
            db,
            function=function,
            action=action,
            prompt=prompt,
            run=suggest_run,
        )
        db.commit()
        db.refresh(run_record)
        return {
            "scope": "function.build",
            "action": action,
            "status": "done",
            "assistant_message": rationale,
            "events": _serialize_function_chat_events(
                [
                    {
                        "type": "phase",
                        "phase": item["phase"],
                        "status": item["status"],
                        "summary": item["summary"],
                        "payload": item.get("payload"),
                        "created_at": item["created_at"],
                    }
                    for item in (_serialize_function_build_run_record(run_record, with_events=True).get("events") or [])
                    if isinstance(item, dict)
                ]
            ),
            "data": {
                "suggestion": suggestion,
                "build_run": _serialize_function_build_run_record(run_record, with_events=True),
            },
        }

    if action == "invoke":
        invoke_payload = payload.get("invoke")
        if not isinstance(invoke_payload, dict):
            raise HTTPException(status_code=400, detail="invoke payload is required")
        trace_id = str(invoke_payload.get("trace_id") or uuid.uuid4())
        runtime_session_factory = sessionmaker(
            bind=db.get_bind(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        write_mode = str(invoke_payload.get("write_mode") or "readonly").strip().lower()
        execution_mode = str(invoke_payload.get("execution_mode") or "apply").strip().lower()
        runtime_path = str(invoke_payload.get("runtime_path") or "production").strip().lower()
        if write_mode not in {"readonly", "write"}:
            raise HTTPException(status_code=400, detail="write_mode must be readonly or write")
        if execution_mode not in {"plan", "apply"}:
            raise HTTPException(status_code=400, detail="execution_mode must be plan or apply")
        if runtime_path not in {"production", "draft"}:
            raise HTTPException(status_code=400, detail="runtime_path must be production or draft")
        if write_mode == "write" and execution_mode == "apply" and not bool(invoke_payload.get("confirm_apply")):
            raise HTTPException(
                status_code=400,
                detail="Writable Function requires explicit confirmation before apply execution (confirm_apply=true)",
            )
        scope_metadata = (
            invoke_payload.get("scope_metadata")
            if isinstance(invoke_payload.get("scope_metadata"), dict)
            else {}
        )
        scope_metadata = {
            **scope_metadata,
            "execution_mode": execution_mode,
            "write_mode": write_mode,
        }
        try:
            result = await chat_agent.invoke_function(
                function=function,
                runtime_session_factory=runtime_session_factory,
                command=FunctionInvokeCommand(
                    payload=invoke_payload.get("payload") or {},
                    runtime_path=runtime_path,
                    datasource_id=invoke_payload.get("datasource_id"),
                    scope_metadata=scope_metadata,
                    timeout_seconds=float(invoke_payload.get("timeout_seconds", 30.0)),
                    trace_id=trace_id,
                ),
                context=context,
            )
        except LifecycleValidationError as err:
            _raise_lifecycle_http_error(err)
        status_text = str(result.status or "unknown")
        if status_text == "success":
            assistant_message = _friendly_invoke_success_message(result.output)
            error_message = None
        else:
            error_message = _friendly_invoke_error_message(
                str(result.error_message or "runtime error"),
                error_code=str(result.error_code or ""),
            )
            assistant_message = error_message
        invoke_run = FunctionBuildRunResult(
            run_id=f"fbr_{uuid.uuid4().hex[:16]}",
            status="done" if status_text == "success" else "failed",
            phase="invoke_finished",
            summary=assistant_message,
            draft_code=str(function.draft_code or ""),
            draft_dependencies=function.draft_dependencies if isinstance(function.draft_dependencies, dict) else {},
            events=[
                FunctionBuildRunEvent(
                    phase="invoke_finished",
                    status=status_text,
                    summary=assistant_message,
                    payload={
                        "duration_ms": result.duration_ms,
                        "run_id": result.run_id,
                        "error_class": result.error_class,
                        "error_code": result.error_code,
                    },
                    created_at=_utc_now_naive().isoformat(),
                )
            ],
            error_summary=None if status_text == "success" else error_message,
        )
        run_record = _persist_function_build_run(
            db,
            function=function,
            action=action,
            prompt=str(invoke_payload.get("prompt") or "invoke"),
            run=invoke_run,
        )
        db.commit()
        db.refresh(run_record)
        return {
            "scope": "function.build",
            "action": action,
            "status": status_text,
            "assistant_message": assistant_message,
            "events": _serialize_function_chat_events(
                [
                    {
                        "type": "phase",
                        "phase": "invoke_finished",
                        "status": status_text,
                        "summary": assistant_message,
                        "payload": {
                            "duration_ms": result.duration_ms,
                            "run_id": result.run_id,
                            "error_class": result.error_class,
                            "error_code": result.error_code,
                        },
                        "created_at": _utc_now_naive().isoformat(),
                    }
                ]
            ),
            "data": {
                "trace_id": trace_id,
                "run_id": result.run_id,
                "status": result.status,
                "duration_ms": result.duration_ms,
                "output": result.output,
                "error_class": result.error_class,
                "error_code": result.error_code,
                "error_message": error_message,
                "build_run": _serialize_function_build_run_record(run_record, with_events=True),
            },
        }

    raise HTTPException(status_code=400, detail="unsupported action")


@router.post("/{function_id}/chat/stream")
async def run_function_chat_action_stream(function_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    _get_function_or_404(db, function_id)
    action = str(payload.get("action") or "build").strip().lower()
    if action not in {"build", "suggest_input", "invoke"}:
        raise HTTPException(status_code=400, detail="unsupported action")
    prompt = str(payload.get("prompt") or "").strip()
    scene_agent, scene_payload = _resolve_function_scene_agent(payload, function_id=function_id)
    conversation_context = _merge_function_scene_context(
        conversation_context=str(payload.get("conversation_context") or ""),
        scene_agent=scene_agent,
        scene_payload=scene_payload,
    )
    agent_id = int(payload.get("agent_id") or 0) or None
    if action == "build" and not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    bind = db.get_bind()
    runtime_session_factory = sessionmaker(
        bind=bind,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    trace_id = str(uuid.uuid4())
    sequence = 0

    def enqueue(event: dict[str, Any]) -> None:
        nonlocal sequence
        sequence += 1
        envelope = {
            "id": f"{trace_id}:{sequence}",
            "seq": sequence,
            **event,
        }
        loop.call_soon_threadsafe(queue.put_nowait, envelope)

    def phase_sink(event: dict[str, Any]) -> None:
        enqueue(
            _to_function_stream_phase_event(
                phase=str(event.get("phase") or ""),
                status=str(event.get("status") or ""),
                summary=str(event.get("summary") or ""),
                payload=event.get("payload") if isinstance(event.get("payload"), dict) else None,
                created_at=str(event.get("created_at") or ""),
            )
        )

    def worker() -> None:
        local_db = runtime_session_factory()
        try:
            if action == "build":
                function = _get_function_or_404(local_db, function_id)
                _guard_not_builtin(function, action="rebuilt")
                configured_skill_names: list[str] = []
                if agent_id:
                    db_agent = local_db.get(models.Agent, agent_id)
                    if db_agent and isinstance(db_agent.skills, list):
                        configured_skill_names = [str(s) for s in db_agent.skills if isinstance(s, str)]
                configured_skill_names = list(
                    dict.fromkeys(
                        configured_skill_names
                        + _resolve_function_scene_skills(scene_agent=scene_agent, scene_payload=scene_payload)
                    )
                )
                skill_result = _run_async_safely(
                    select_skills_for_context(
                        prompt=prompt,
                        recent_context=[],
                        configured_skill_names=configured_skill_names,
                        context_label=f"function:{function_id}",
                    )
                )
                active_skills: list[str] = skill_result.get("active_skills") or []
                if active_skills:
                    enqueue(
                        {
                            "type": "skill_delta",
                            "data": {
                                "active_skills": active_skills,
                                "added": skill_result.get("added") or [],
                                "removed": skill_result.get("removed") or [],
                                "reason": skill_result.get("reason") or "",
                                "selector_ok": skill_result.get("selector_ok", True),
                                "source": "skill_selector",
                                "agent": "SkillSelector",
                            },
                        }
                    )
                skill_context = format_skill_context(active_skills)
                build_run, run_record = _run_function_build_action(
                    db=local_db,
                    function=function,
                    prompt=prompt,
                    conversation_context=conversation_context,
                    skill_context=skill_context,
                    action="build",
                    event_sink=phase_sink,
                )
                assistant_message = str(build_run.summary or "").strip()
                if assistant_message:
                    enqueue(
                        {
                            "type": "assistant",
                            "event_group": "core",
                            "event_name": "assistant",
                            "phase": "responding",
                            "data": {"text": assistant_message, "source": "llm", "agent": "FunctionBuilderAgent"},
                        }
                    )
                verification_payload: dict[str, Any] | None = None
                for event in (build_run.events or []):
                    if not isinstance(event.payload, dict):
                        continue
                    if str(event.phase or "").strip().lower() not in {"apply", "verify_failed"}:
                        continue
                    if isinstance(event.payload.get("verification"), dict):
                        verification_payload = event.payload.get("verification")
                        break
                if verification_payload is not None:
                    verify_passed = bool(verification_payload.get("passed"))
                    enqueue(
                        {
                            "type": "extension",
                            "event_group": "extension",
                            "event_name": "verify_result",
                            "data": {
                                "summary": "Business result verification passed" if verify_passed else "Business result verification failed",
                                "verification": verification_payload,
                                "source": "verifier",
                                "agent": "FunctionVerifier",
                            },
                        }
                    )
                enqueue(
                    {
                        "type": "done",
                        "event_group": "core",
                        "event_name": "done",
                        "data": {
                            "trace_id": trace_id,
                            "scope": "function.build",
                            "action": "build",
                            "status": build_run.status,
                            "assistant_message": assistant_message,
                            "source": "runtime",
                            "agent": "FunctionBuildOrchestrator",
                            "function": _serialize(function),
                            "build_run": _serialize_function_build_run_record(run_record, with_events=True),
                        },
                    }
                )
            else:
                result = _run_async_safely(
                    run_function_chat_action(function_id=function_id, payload=payload, db=local_db)
                )
                if not isinstance(result, dict):
                    raise ValueError("stream action result is invalid")
                events = result.get("events")
                if isinstance(events, list):
                    for item in events:
                        if not isinstance(item, dict):
                            continue
                        enqueue(
                            _to_function_stream_phase_event(
                                phase=str(item.get("phase") or ""),
                                status=str(item.get("status") or ""),
                                summary=str(item.get("summary") or ""),
                                payload=item.get("payload") if isinstance(item.get("payload"), dict) else None,
                                created_at=str(item.get("created_at") or ""),
                            )
                        )
                assistant_message = str(result.get("assistant_message") or "").strip()
                if assistant_message:
                    enqueue(
                        {
                            "type": "assistant",
                            "event_group": "core",
                            "event_name": "assistant",
                            "phase": "responding",
                            "data": {"text": assistant_message, "source": "llm", "agent": "FunctionBuilderAgent"},
                        }
                    )
                enqueue(
                    {
                        "type": "done",
                        "event_group": "core",
                        "event_name": "done",
                        "data": {
                            "trace_id": trace_id,
                            "scope": str(result.get("scope") or "function.build"),
                            "action": str(result.get("action") or action),
                            "status": str(result.get("status") or "done"),
                            "assistant_message": assistant_message,
                            "source": "runtime",
                            "agent": "FunctionBuildOrchestrator",
                            **(
                                result.get("data")
                                if isinstance(result.get("data"), dict)
                                else {}
                            ),
                        },
                    }
                )
        except HTTPException as exc:
            enqueue(
                {
                    "type": "error",
                    "event_group": "core",
                    "event_name": "error",
                    "data": {
                        "message": str(exc.detail),
                        "error_class": "http_exception",
                        "status_code": exc.status_code,
                        "source": "runtime",
                        "agent": "FunctionBuildOrchestrator",
                    },
                }
            )
        except Exception as exc:  # pragma: no cover - protected by e2e tests
            enqueue(
                {
                    "type": "error",
                    "event_group": "core",
                    "event_name": "error",
                    "data": {
                        "message": str(exc),
                        "error_class": exc.__class__.__name__,
                        "source": "runtime",
                        "agent": "FunctionBuildOrchestrator",
                    },
                }
            )
        finally:
            try:
                local_db.close()
            except Exception:
                pass
            loop.call_soon_threadsafe(queue.put_nowait, None)

    async def generate():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {_json_dumps_safe(item)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{function_id}/suggest-input")
def suggest_function_input(function_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    function = _get_function_or_404(db, function_id)
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        prompt = "Generate directly runnable test input based on the current Function."
    conversation_context = str(payload.get("conversation_context") or "")
    chat_agent = FunctionChatAgent()
    suggestion = chat_agent.suggest_function_input(
        function=function,
        prompt=prompt,
        conversation_context=conversation_context,
        context=ScopedChatContext(scope=ChatScope.FUNCTION_BUILD),
    )
    return suggestion


@router.delete("/{function_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_function(function_id: int, db: Session = Depends(get_db)):
    function = _get_function_or_404(db, function_id)
    _guard_not_builtin(function, action="deleted")
    db.delete(function)
    db.commit()
    return None


@router.post("/{function_id}/duplicate", status_code=status.HTTP_201_CREATED)
def duplicate_function(function_id: int, db: Session = Depends(get_db)):
    source = _get_function_or_404(db, function_id)
    base_name = f"{source.name or 'Function'} (Copy)"
    slug = _generate_function_slug(db, display_name=base_name)
    copy_fn = models.Function(
        name=base_name,
        slug=slug,
        description=source.description,
        kind="custom",
        status="draft",
        draft_code=source.draft_code,
        draft_dependencies=copy.deepcopy(source.draft_dependencies) if isinstance(source.draft_dependencies, dict) else source.draft_dependencies,
    )
    db.add(copy_fn)
    db.commit()
    db.refresh(copy_fn)
    main_path = WorkspaceStore().sync_function_draft(copy_fn)
    copy_fn.source_path = str(main_path)
    copy_fn.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(copy_fn)
    return _serialize(copy_fn)


@router.get("/{function_id}/releases")
def list_function_releases(function_id: int, db: Session = Depends(get_db)):
    _get_function_or_404(db, function_id)
    releases = (
        db.query(models.FunctionRelease)
        .filter(models.FunctionRelease.function_id == function_id)
        .order_by(models.FunctionRelease.version.desc())
        .all()
    )
    return [_serialize(item) for item in releases]


@router.get("/{function_id}/build-runs")
def list_function_build_runs(function_id: int, limit: int = 20, db: Session = Depends(get_db)):
    _get_function_or_404(db, function_id)
    normalized_limit = max(1, min(limit, 200))
    rows = (
        db.query(models.FunctionBuildRun)
        .filter(models.FunctionBuildRun.function_id == function_id)
        .order_by(models.FunctionBuildRun.created_at.desc())
        .limit(normalized_limit)
        .all()
    )
    return [_serialize_function_build_run_record(item) for item in rows]


@router.get("/{function_id}/build-runs/{run_id}")
def get_function_build_run(function_id: int, run_id: str, db: Session = Depends(get_db)):
    _get_function_or_404(db, function_id)
    row = (
        db.query(models.FunctionBuildRun)
        .filter(models.FunctionBuildRun.function_id == function_id, models.FunctionBuildRun.run_id == run_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Function build run {run_id} not found")
    return _serialize_function_build_run_record(row, with_events=True)


@router.get("/{function_id}/build-runs/{run_id}/events")
def list_function_build_run_events(function_id: int, run_id: str, db: Session = Depends(get_db)):
    _get_function_or_404(db, function_id)
    row = (
        db.query(models.FunctionBuildRun)
        .filter(models.FunctionBuildRun.function_id == function_id, models.FunctionBuildRun.run_id == run_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Function build run {run_id} not found")
    events = sorted(row.events, key=lambda event: event.created_at)
    return [_serialize(item) for item in events]


@router.get("/{function_id}/runs")
def list_function_runs(function_id: int, limit: int = 20, db: Session = Depends(get_db)):
    _get_function_or_404(db, function_id)
    normalized_limit = max(1, min(limit, 200))
    runs = (
        db.query(models.FunctionRun)
        .filter(models.FunctionRun.function_id == function_id)
        .order_by(models.FunctionRun.created_at.desc())
        .limit(normalized_limit)
        .all()
    )
    return [_serialize(item) for item in runs]


@router.post("/{function_id}/strategy")
def decide_function_strategy(function_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    function = _get_function_or_404(db, function_id)
    default_contract = _derive_contract_from_dependency_manifest(
        function.draft_dependencies if isinstance(function.draft_dependencies, dict) else {},
    )
    input_contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else None
    decider = FunctionStrategyDecider()
    decision = decider.decide(
        db,
        requirement_text=str(payload.get("requirement") or function.description or function.name or ""),
        contract=input_contract if input_contract is not None else (default_contract if default_contract else None),
        exclude_function_id=function.id,
        force_strategy=payload.get("force_strategy") if isinstance(payload.get("force_strategy"), str) else None,
        thresholds=StrategyThresholds(
            reuse=float(payload.get("reuse_threshold", 0.82)),
            extend=float(payload.get("extend_threshold", 0.45)),
        ),
    )
    return {
        "function_id": function.id,
        **decision,
    }


@router.post("/{function_id}/verify")
def verify_function(function_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    function = _get_function_or_404(db, function_id)
    verifier = FunctionVerificationHarness()
    report = verifier.verify_draft(
        code_snapshot=str(payload.get("code_snapshot") or function.draft_code or ""),
        dependency_manifest=payload.get("dependency_manifest") or function.draft_dependencies,
    )
    return {
        "function_id": function.id,
        "verification": report,
    }


@router.post("/{function_id}/release")
def release_function(function_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    lifecycle = FunctionLifecycleService()
    verifier = FunctionVerificationHarness()
    decider = FunctionStrategyDecider()
    function = _get_function_or_404(db, function_id)

    code_snapshot = payload.get("code_snapshot") or function.draft_code
    if not code_snapshot:
        raise HTTPException(status_code=400, detail="code_snapshot is required for release")

    dependency_manifest = payload.get("dependency_manifest") or function.draft_dependencies
    verification = verifier.verify_draft(
        code_snapshot=code_snapshot,
        dependency_manifest=dependency_manifest,
    )
    if not verification["passed"]:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "verification_failed",
                "message": "Function verification failed before release",
                "diagnostics": verification["diagnostics"],
                "checks": verification["checks"],
            },
        )
    governed_dependencies, governance_snapshot = _apply_verification_governance_to_dependencies(
        function=function,
        prompt=str(payload.get("requirement") or function.description or function.name or ""),
        dependencies=dependency_manifest if isinstance(dependency_manifest, dict) else {},
        verification=verification,
        tests_suggested=[],
    )
    function.draft_dependencies = governed_dependencies
    dependency_manifest = governed_dependencies
    derived_contract = _derive_contract_from_dependency_manifest(
        dependency_manifest,
        capability_profile=verification.get("capability_profile"),
    )

    decision = decider.decide(
        db,
        requirement_text=str(payload.get("requirement") or function.description or function.name or ""),
        contract=(
            payload.get("contract")
            if isinstance(payload.get("contract"), dict)
            else (derived_contract if derived_contract else None)
        ),
        exclude_function_id=function.id,
        force_strategy=payload.get("force_strategy") if isinstance(payload.get("force_strategy"), str) else None,
        thresholds=StrategyThresholds(
            reuse=float(payload.get("reuse_threshold", 0.82)),
            extend=float(payload.get("extend_threshold", 0.45)),
        ),
    )

    release_metadata = payload.get("release_metadata")
    if release_metadata is None:
        release_metadata = {}
    if not isinstance(release_metadata, dict):
        raise HTTPException(status_code=400, detail="release_metadata must be an object")
    release_metadata = {
        **release_metadata,
        "contract": release_metadata.get("contract") if isinstance(release_metadata.get("contract"), dict) else derived_contract,
        "strategy_decision": decision,
        "verification": verification,
        "capability_profile": verification.get("capability_profile"),
        "capability_governance": governance_snapshot,
    }
    try:
        release = lifecycle.release(
            function,
            code_snapshot=code_snapshot,
            dependency_manifest=dependency_manifest,
            release_metadata=release_metadata,
        )
    except LifecycleValidationError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    function.updated_at = _utc_now_naive()
    main_path = WorkspaceStore().sync_function_draft(function)
    function.source_path = str(main_path)
    commit_sha = WorkspaceStore().commit_publish(
        object_type="function",
        object_id=function.id,
        action="release",
        summary=str(function.description or function.name or ""),
    )
    function.current_commit_sha = commit_sha
    function.release_commit_sha = commit_sha
    db.commit()
    db.refresh(function)
    db.refresh(release)
    return {
        "function": _serialize(function),
        "release": _serialize(release),
        "strategy": decision["strategy"],
    }


@router.post("/{function_id}/invoke")
async def invoke_function(function_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    function = _get_function_or_404(db, function_id)
    trace_id = str(payload.get("trace_id") or uuid.uuid4())
    runtime_session_factory = sessionmaker(
        bind=db.get_bind(),
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    chat_agent = FunctionChatAgent()
    write_mode = str(payload.get("write_mode") or "readonly").strip().lower()
    execution_mode = str(payload.get("execution_mode") or "apply").strip().lower()
    runtime_path = str(payload.get("runtime_path") or "production").strip().lower()
    if write_mode not in {"readonly", "write"}:
        raise HTTPException(status_code=400, detail="write_mode must be readonly or write")
    if execution_mode not in {"plan", "apply"}:
        raise HTTPException(status_code=400, detail="execution_mode must be plan or apply")
    if runtime_path not in {"production", "draft"}:
        raise HTTPException(status_code=400, detail="runtime_path must be production or draft")
    if write_mode == "write" and execution_mode == "apply" and not bool(payload.get("confirm_apply")):
        raise HTTPException(
            status_code=400,
            detail="Writable Function requires explicit confirmation before apply execution (confirm_apply=true)",
        )
    scope_metadata = payload.get("scope_metadata") if isinstance(payload.get("scope_metadata"), dict) else {}
    scope_metadata = {
        **scope_metadata,
        "execution_mode": execution_mode,
        "write_mode": write_mode,
    }
    try:
        result = await chat_agent.invoke_function(
            function=function,
            runtime_session_factory=runtime_session_factory,
            command=FunctionInvokeCommand(
                payload=payload.get("payload") or {},
                runtime_path=runtime_path,
                datasource_id=payload.get("datasource_id"),
                scope_metadata=scope_metadata,
                timeout_seconds=float(payload.get("timeout_seconds", 30.0)),
                trace_id=trace_id,
            ),
            context=ScopedChatContext(scope=ChatScope.FUNCTION_BUILD),
        )
    except LifecycleValidationError as err:
        _raise_lifecycle_http_error(err)
    friendly_error_message = None
    if str(result.status or "").strip().lower() != "success":
        friendly_error_message = _friendly_invoke_error_message(
            str(result.error_message or "runtime error"),
            error_code=str(result.error_code or ""),
        )
    return {
        "trace_id": trace_id,
        "run_id": result.run_id,
        "status": result.status,
        "duration_ms": result.duration_ms,
        "output": result.output,
        "error_class": result.error_class,
        "error_code": result.error_code,
        "error_message": friendly_error_message,
        "execution_mode": execution_mode,
        "write_mode": write_mode,
        "runtime_path": runtime_path,
    }
