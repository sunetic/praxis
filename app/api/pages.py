from __future__ import annotations

import asyncio
import json
import threading
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models import models
from app.services.page.builder import (
    PageBuilderService,  # noqa: F401 - kept for test monkeypatch hook
    PageBuildRunEvent,
    PageBuildRunResult,
)
from app.services.lifecycle import LifecycleValidationError, PageLifecycleService, PageState
from app.services.page.authoring_agent import PageBuildCommand, PagePlanner
from app.services.page.build_orchestrator import (
    PageBuildOrchestratorCommand,
    PageBuilderOrchestrator,
)
from app.services.page.review_evidence import normalize_page_semantic_review_config
from app.services.page.preview_theme import ensure_page_preview_theme
from app.services.platform.workspace_store import WorkspaceStore

router = APIRouter(prefix="/pages", tags=["Pages"])
logger = get_logger("app.api.pages")
_DEFAULT_PAGE_BUILDER_SERVICE_CLASS = PageBuilderService
_DEFAULT_WORKSPACE_STORE_CLASS = WorkspaceStore

_STATS_PROVIDER_MARKER = "[page-stats-collection-provider:v1]"
_STATS_PROVIDER_NAME = "集群统计信息收集状态查询"
_STATS_PROVIDER_DEPENDENCY_KEY = "stats_collection_provider"
_PAGE_ORCHESTRATION_MODE_SINGLE_SCENARIO = "single_scenario"
_PAGE_CORE_PHASES = {"plan", "act", "observe", "reflect", "retry"}
_PAGE_STREAM_EVENT_SINK_KEY = "__internal_event_sink__"


def _serialize(record: Any) -> dict[str, Any]:
    return json.loads(json.dumps(
        {column.name: getattr(record, column.name) for column in record.__table__.columns},
        default=str,
        ensure_ascii=False,
    )) 


def _json_dumps_safe(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _should_use_legacy_page_builder() -> bool:
    return PageBuilderService is not _DEFAULT_PAGE_BUILDER_SERVICE_CLASS


def _should_use_orchestrated_page_builder(payload: dict[str, Any]) -> bool:
    build_mode = str(payload.get("build_mode") or "").strip().lower()
    orchestration = payload.get("orchestration")
    orchestration_enabled = isinstance(orchestration, dict) and bool(orchestration.get("enabled"))
    semantic_review_enabled = (
        isinstance(orchestration, dict)
        and isinstance(orchestration.get("semantic_review"), dict)
        and bool((orchestration.get("semantic_review") or {}).get("enabled"))
    )
    workspace_store_overridden = WorkspaceStore is not _DEFAULT_WORKSPACE_STORE_CLASS
    return build_mode == "coding_engine" or orchestration_enabled or semantic_review_enabled or workspace_store_overridden


def _run_legacy_page_builder(
    *,
    db: Session,
    page: models.Page,
    prompt: str,
    event_sink: Any = None,
) -> dict[str, Any]:
    builder = PageBuilderService()
    previous_draft_payload = deepcopy(page.draft_payload) if isinstance(page.draft_payload, dict) else {}
    build = builder.apply_prompt(previous_draft_payload, prompt)
    summary_text = str(build.summary or "页面草稿已更新。").strip() or "页面草稿已更新。"
    next_draft_payload = deepcopy(build.draft_payload) if isinstance(build.draft_payload, dict) else previous_draft_payload
    build_run = PageBuildRunResult(
        run_id=f"pbr_{uuid4().hex[:16]}",
        status="done",
        phase="apply",
        summary=summary_text,
        draft_payload=next_draft_payload,
        events=[
            PageBuildRunEvent(
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
    page.draft_payload = next_draft_payload
    if page.status == PageState.PUBLISHED.value:
        PageLifecycleService().transition(page, target_state=PageState.DRAFT)
    page.updated_at = _utc_now_naive()
    run_record = _persist_page_build_run(
        db,
        page=page,
        prompt=prompt,
        run=build_run,
    )
    db.commit()
    db.refresh(page)
    db.refresh(run_record)
    main_path = WorkspaceStore().sync_page_draft(page)
    page.source_path = str(main_path)
    page.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(page)
    if callable(event_sink):
        _emit_page_phase_event(
            event_sink,
            phase="apply",
            status="done",
            summary=summary_text,
            payload={"legacy_builder": True},
        )
    return {
        "page": _serialize(page),
        "build_summary": summary_text,
        "build_run": _serialize_build_run(run_record, with_events=True),
    }


def _normalize_page_core_phase(phase: str) -> str:
    normalized = str(phase or "").strip().lower()
    if normalized in _PAGE_CORE_PHASES:
        return normalized
    if normalized in {"intake", "intent_parsed", "draft_planned", "plan_generated", "reuse_recommendation"}:
        return "plan"
    if normalized in {"code_generated", "patch_applied", "apply", "invoke", "invoke_started", "suggest_input"}:
        return "act"
    if normalized in {"patch_validated", "preview_ready", "verify_failed", "failed", "invoke_finished"}:
        return "observe"
    return "act"


def _to_page_stream_core_event(
    *,
    phase: str,
    status: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    core_phase = _normalize_page_core_phase(phase)
    data_payload = dict(payload) if isinstance(payload, dict) else {}
    raw_phase = str(phase or "").strip()
    if raw_phase and raw_phase != core_phase:
        data_payload["raw_phase"] = raw_phase
    source, agent = _page_event_origin(core_phase)
    data_payload.setdefault("source", source)
    data_payload.setdefault("agent", agent)
    event_data: dict[str, Any] = {
        "status": str(status or "").strip() or "running",
        "summary": str(summary or "").strip(),
        "payload": data_payload,
        "created_at": str(created_at or _utc_now_naive().isoformat()),
    }
    return {
        "type": "phase",
        "event_group": "core",
        "event_name": core_phase,
        "phase": core_phase,
        "data": event_data,
    }


def _page_event_origin(core_phase: str) -> tuple[str, str]:
    normalized = str(core_phase or "").strip().lower()
    if normalized == "plan":
        return "llm", "PagePlanner"
    if normalized == "act":
        return "runtime", "PageBuilderAgent"
    if normalized == "observe":
        return "verifier", "PageVerifier"
    if normalized in {"reflect", "retry"}:
        return "llm", "PageBuilderOrchestrator"
    return "runtime", "PageBuilderOrchestrator"


def _emit_page_phase_event(
    event_sink: Any,
    *,
    phase: str,
    status: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if event_sink is None:
        return
    event_payload = dict(payload) if isinstance(payload, dict) else {}
    source, agent = _page_event_origin(_normalize_page_core_phase(phase))
    event_payload.setdefault("source", source)
    event_payload.setdefault("agent", agent)
    event: dict[str, Any] = {
        "type": "phase",
        "phase": str(phase or "").strip(),
        "status": str(status or "").strip(),
        "summary": str(summary or "").strip(),
        "payload": event_payload,
        "created_at": _utc_now_naive().isoformat(),
    }
    try:
        event_sink(event)
    except Exception:
        return


def _build_attempt_core_events(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(attempts) <= 1:
        return []
    normalized_attempts = [item for item in attempts if isinstance(item, dict)]
    if len(normalized_attempts) <= 1:
        return []
    events: list[dict[str, Any]] = []
    for index, attempt in enumerate(normalized_attempts):
        attempt_no = int(attempt.get("attempt") or (index + 1))
        status = str(attempt.get("status") or "").strip().lower()
        summary = str(attempt.get("summary") or "").strip()
        diagnostics = [
            str(item).strip()
            for item in (attempt.get("diagnostics") or [])
            if str(item).strip()
        ]
        if status in {"failed", "error"}:
            events.append(
                _to_page_stream_core_event(
                    phase="observe",
                    status="failed",
                    summary=f"Observe · Attempt {attempt_no} 失败{f'：{summary}' if summary else ''}",
                    payload={"attempt": attempt_no, "diagnostics": diagnostics[:3]},
                )
            )
            if diagnostics:
                events.append(
                    _to_page_stream_core_event(
                        phase="reflect",
                        status="noted",
                        summary=f"Reflect · {diagnostics[0]}",
                        payload={"attempt": attempt_no, "reason": diagnostics[0]},
                    )
                )
            if index < len(normalized_attempts) - 1:
                events.append(
                    _to_page_stream_core_event(
                        phase="retry",
                        status="running",
                        summary=f"Retry · 发起 Attempt {attempt_no + 1}",
                        payload={"attempt": attempt_no + 1},
                    )
                )
            continue
        events.append(
            _to_page_stream_core_event(
                phase="act",
                status="done",
                summary=f"Act · Attempt {attempt_no} 成功{f'：{summary}' if summary else ''}",
                payload={"attempt": attempt_no},
            )
        )
    return events


def _derive_page_stream_events(result_payload: dict[str, Any]) -> list[dict[str, Any]]:
    build_run = result_payload.get("build_run") if isinstance(result_payload.get("build_run"), dict) else {}
    events = build_run.get("events") if isinstance(build_run.get("events"), list) else []
    normalized: list[dict[str, Any]] = []
    extension_emitted = {"verify_result": False, "preview_result": False}

    for item in events:
        if not isinstance(item, dict):
            continue
        phase = str(item.get("phase") or "").strip()
        status = str(item.get("status") or "").strip()
        summary = str(item.get("summary") or "").strip()
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        created_at = str(item.get("created_at") or "")
        if phase:
            if phase == "apply":
                attempts = payload.get("attempts") if isinstance(payload.get("attempts"), list) else []
                normalized.extend(_build_attempt_core_events(attempts))
            normalized.append(
                _to_page_stream_core_event(
                    phase=phase,
                    status=status,
                    summary=summary,
                    payload=payload if isinstance(payload, dict) else None,
                    created_at=created_at,
                )
            )
        verification = payload.get("verification") if isinstance(payload.get("verification"), dict) else None
        if verification is not None and not extension_emitted["verify_result"]:
            verify_passed = bool(
                (verification.get("page") or {}).get("passed")
                if isinstance(verification.get("page"), dict)
                else verification.get("passed")
            )
            normalized.append(
                {
                    "type": "extension",
                    "event_group": "extension",
                    "event_name": "verify_result",
                    "data": {
                        "summary": "业务结果校验通过" if verify_passed else "业务结果校验未通过",
                        "verification": verification,
                    },
                }
            )
            extension_emitted["verify_result"] = True
        if phase in {"apply", "preview_ready"} and not extension_emitted["preview_result"]:
            page_payload = result_payload.get("page") if isinstance(result_payload.get("page"), dict) else {}
            draft = page_payload.get("draft_payload") if isinstance(page_payload.get("draft_payload"), dict) else {}
            config = draft.get("config") if isinstance(draft.get("config"), dict) else {}
            runtime = draft.get("runtime") if isinstance(draft.get("runtime"), dict) else {}
            normalized.append(
                {
                    "type": "extension",
                    "event_group": "extension",
                    "event_name": "preview_result",
                    "data": {
                        "summary": "Preview · 预览已刷新",
                        "title": str(config.get("title") or ""),
                        "has_preview_html": bool(str(runtime.get("preview_html") or "").strip()),
                    },
                }
            )
            extension_emitted["preview_result"] = True

    if not normalized:
        normalized.append(
            _to_page_stream_core_event(
                phase="act",
                status=str(build_run.get("status") or "done"),
                summary=str(build_run.get("result_summary") or build_run.get("error_summary") or "页面草稿已更新"),
                payload={},
            )
        )
    return normalized


def _get_page_or_404(db: Session, page_id: int) -> models.Page:
    page = db.query(models.Page).filter(models.Page.id == page_id).first()
    if page is None:
        raise HTTPException(status_code=404, detail=f"Page {page_id} not found")
    return page


def _get_page_build_run_or_404(db: Session, page_id: int, run_id: str) -> models.PageBuildRun:
    run = (
        db.query(models.PageBuildRun)
        .filter(models.PageBuildRun.page_id == page_id, models.PageBuildRun.run_id == run_id)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail=f"Page build run {run_id} not found")
    return run


def _serialize_build_run(run: models.PageBuildRun, *, with_events: bool = False) -> dict[str, Any]:
    payload = _serialize(run)
    if with_events:
        payload["events"] = [
            _serialize(item)
            for item in sorted(run.events, key=lambda event: event.created_at)
        ]
    return payload


def _serialize_compile_run(run: models.PageCompileRun) -> dict[str, Any]:
    payload = _serialize(run)
    payload["snapshot"] = _serialize(run.snapshot) if run.snapshot is not None else None
    return payload


def _persist_page_build_run(
    db: Session,
    *,
    page: models.Page,
    prompt: str,
    run: PageBuildRunResult,
) -> models.PageBuildRun:
    run_record = models.PageBuildRun(
        run_id=run.run_id,
        page_id=page.id,
        status=run.status,
        phase=run.phase,
        prompt=prompt,
        result_summary=run.summary if run.status == "done" else None,
        error_summary=run.error_summary,
        finished_at=_utc_now_naive(),
    )
    db.add(run_record)
    db.flush()

    for event in run.events:
        db.add(
            models.PageBuildEvent(
                build_run_id=run_record.id,
                phase=event.phase,
                status=event.status,
                summary=event.summary,
                payload=event.payload,
            )
        )
    return run_record


def _build_page_clarification_run(
    *,
    prompt: str,
    draft_payload: dict[str, Any] | None,
    summary: str,
    detail_payload: dict[str, Any],
) -> PageBuildRunResult:
    return PageBuildRunResult(
        run_id=f"pbr_{uuid4().hex[:16]}",
        status="needs_clarification",
        phase="intake",
        summary=summary,
        draft_payload=deepcopy(draft_payload) if isinstance(draft_payload, dict) else {},
        events=[
            PageBuildRunEvent(
                phase="intake",
                status="needs_clarification",
                summary=summary,
                created_at=_utc_now_naive().isoformat(),
                payload={
                    "prompt": str(prompt or "").strip(),
                    **(detail_payload if isinstance(detail_payload, dict) else {}),
                },
            )
        ],
        error_summary=None,
    )


def _compile_page_artifact(snapshot_payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not (isinstance(snapshot_payload, dict) and snapshot_payload.get("version") == "page-runtime-v2"):
        raise ValueError("snapshot payload must be page-runtime-v2")
    runtime = snapshot_payload.get("runtime") if isinstance(snapshot_payload.get("runtime"), dict) else {}
    source = snapshot_payload.get("source") if isinstance(snapshot_payload.get("source"), dict) else {}
    config = snapshot_payload.get("config") if isinstance(snapshot_payload.get("config"), dict) else {}
    preview_html = str(runtime.get("preview_html") or "").strip()
    source_code = str(source.get("code") or "").strip()
    if not preview_html:
        raise ValueError("runtime preview_html is required for compile")
    if not source_code:
        raise ValueError("runtime source code is required for compile")
    preview_html = ensure_page_preview_theme(preview_html)
    artifact = {
        "version": "page-artifact-v2",
        "kind": "runtime_page",
        "compiled_at": str(_utc_now_naive()),
        "config": {
            "title": str(config.get("title") or ""),
            "description": str(config.get("description") or ""),
        },
        "runtime": {
            "framework": str(runtime.get("framework") or "html"),
            "preview_html": preview_html,
        },
        "source": {
            "language": str(source.get("language") or "tsx"),
            "code": source_code,
        },
    }
    title = str(config.get("title") or "Untitled")
    return artifact, f"已编译运行时页面（{title}）"


def _append_page_build_history(
    draft_payload: dict[str, Any] | None,
    *,
    prompt: str,
    summary: str,
) -> dict[str, Any]:
    payload = deepcopy(draft_payload) if isinstance(draft_payload, dict) else {}
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    history_raw = meta.get("history")
    history = [item for item in history_raw if isinstance(item, dict)] if isinstance(history_raw, list) else []
    entry = {
        "prompt": str(prompt or "").strip(),
        "summary": str(summary or "").strip(),
        "created_at": _utc_now_naive().isoformat(),
    }
    history.append(entry)
    meta["history"] = history[-50:]
    meta["last_prompt"] = entry["prompt"]
    meta["summary"] = entry["summary"]
    meta["updated_at"] = entry["created_at"]
    payload["meta"] = meta
    return payload


def _load_recent_page_build_contexts(db: Session, *, page_id: int, limit: int = 6) -> list[dict[str, str]]:
    rows = (
        db.query(models.PageBuildRun)
        .filter(models.PageBuildRun.page_id == page_id)
        .order_by(models.PageBuildRun.created_at.desc())
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


def _compose_page_engine_goal(
    user_prompt: str,
    *,
    recent_contexts: list[dict[str, str]] | None = None,
    conversation_context: str = "",
) -> str:
    planner = PagePlanner()
    plan = planner.plan(
        command=PageBuildCommand(
            prompt=str(user_prompt or ""),
            conversation_context=conversation_context,
            recent_contexts=[item for item in (recent_contexts or []) if isinstance(item, dict)],
            orchestration=None,
        )
    )
    return str(plan.goal or "")


def _normalize_dependency_key(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    alias_map = {
        "stats_provider": _STATS_PROVIDER_DEPENDENCY_KEY,
        "stats_collection_status": _STATS_PROVIDER_DEPENDENCY_KEY,
        "cluster_stats_collection_status": _STATS_PROVIDER_DEPENDENCY_KEY,
    }
    return alias_map.get(normalized, normalized)


def _slot_has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _normalize_page_orchestration(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("orchestration") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {
            "enabled": False,
            "mode": "legacy",
            "scenario_id": "",
            "required_slots": [],
            "slots": {},
            "dependencies": [],
            "semantic_review": None,
        }

    mode = str(raw.get("mode") or "").strip().lower() or _PAGE_ORCHESTRATION_MODE_SINGLE_SCENARIO
    scenario_id = str(raw.get("scenario_id") or "").strip().lower()
    required_slots = []
    for item in raw.get("required_slots") or []:
        key = str(item or "").strip()
        if key:
            required_slots.append(key)
    slots = raw.get("slots") if isinstance(raw.get("slots"), dict) else {}

    dependencies: list[dict[str, Any]] = []
    raw_dependencies = raw.get("dependencies")
    if isinstance(raw_dependencies, list):
        for item in raw_dependencies:
            if isinstance(item, str):
                key = _normalize_dependency_key(item)
                if key:
                    dependencies.append(
                        {
                            "key": key,
                            "strategy": "create_or_reuse",
                            "verify": True,
                            "invoke_payload": {},
                        }
                    )
                continue
            if not isinstance(item, dict):
                continue
            key = _normalize_dependency_key(item.get("key") or item.get("id"))
            if not key:
                continue
            invoke_payload = item.get("invoke_payload") if isinstance(item.get("invoke_payload"), dict) else {}
            dependencies.append(
                {
                    "key": key,
                    "strategy": str(item.get("strategy") or "create_or_reuse"),
                    "verify": bool(item.get("verify", True)),
                    "invoke_payload": invoke_payload,
                }
            )

    if mode == _PAGE_ORCHESTRATION_MODE_SINGLE_SCENARIO and scenario_id == "cluster_stats_collection_status":
        if not any(item.get("key") == _STATS_PROVIDER_DEPENDENCY_KEY for item in dependencies):
            dependencies.append(
                {
                    "key": _STATS_PROVIDER_DEPENDENCY_KEY,
                    "strategy": "create_or_reuse",
                    "verify": True,
                    "invoke_payload": {},
                }
            )

    return {
        "enabled": bool(raw.get("enabled", False)),
        "mode": mode,
        "scenario_id": scenario_id,
        "required_slots": required_slots,
        "slots": slots,
        "dependencies": dependencies,
        "semantic_review": (
            config.__dict__
            if (config := normalize_page_semantic_review_config(raw)) is not None
            else None
        ),
    }


def _collect_missing_required_slots(orchestration: dict[str, Any]) -> list[str]:
    required_slots = orchestration.get("required_slots") if isinstance(orchestration.get("required_slots"), list) else []
    slots = orchestration.get("slots") if isinstance(orchestration.get("slots"), dict) else {}
    missing: list[str] = []
    for raw_key in required_slots:
        key = str(raw_key or "").strip()
        if not key:
            continue
        slot_value = slots.get(key)
        if isinstance(slot_value, dict):
            status = str(slot_value.get("status") or "").strip().lower()
            value = slot_value.get("value")
            confirmed = status == "confirmed" if status else _slot_has_value(value)
            if not (confirmed and _slot_has_value(value)):
                missing.append(key)
            continue
        if not _slot_has_value(slot_value):
            missing.append(key)
    return missing


def _build_stats_provider_code() -> str:
    return (
        "def main(payload, context):\n"
        "    payload = payload or {}\n"
        "    filters = payload.get('filters') if isinstance(payload.get('filters'), dict) else {}\n"
        "    limit = int(payload.get('limit') or 20)\n"
        "    limit = max(1, min(limit, 200))\n"
        "    schedulers = platform.list('scheduler', filters=filters, limit=limit)\n"
        "    status_count = {}\n"
        "    items = []\n"
        "    for item in schedulers:\n"
        "        status = str(item.get('status') or 'unknown')\n"
        "        status_count[status] = int(status_count.get(status, 0)) + 1\n"
        "        items.append(\n"
        "            {\n"
        "                'schedule_id': item.get('id'),\n"
        "                'name': item.get('name'),\n"
        "                'status': status,\n"
        "                'target_type': item.get('target_type'),\n"
        "                'target_id': item.get('target_id'),\n"
        "            }\n"
        "        )\n"
        "    return {\n"
        "        'summary': {\n"
        "            'total': len(items),\n"
        "            'active': int(status_count.get('active', 0)),\n"
        "            'paused': int(status_count.get('paused', 0)),\n"
        "        },\n"
        "        'collection_status': {\n"
        "            'items': items,\n"
        "            'total': len(items),\n"
        "            'filters': filters,\n"
        "        },\n"
        "    }\n"
    )


def _ensure_stats_provider_function(db: Session) -> models.Function:
    existing = (
        db.query(models.Function)
        .filter(models.Function.description.contains(_STATS_PROVIDER_MARKER))
        .order_by(models.Function.updated_at.desc())
        .first()
    )
    if existing is not None:
        return existing

    from app.api import functions as functions_api  # Local import avoids circular import at module load time.

    created = functions_api.create_function(
        {
            "name": _STATS_PROVIDER_NAME,
            "description": _STATS_PROVIDER_MARKER,
            "draft_code": _build_stats_provider_code(),
            "draft_dependencies": {},
        },
        db=db,
    )
    function = db.query(models.Function).filter(models.Function.id == int(created["id"])).first()
    if function is None:
        raise HTTPException(status_code=500, detail="failed to create stats provider function")
    return function


def _invoke_function_sync(function_id: int, payload: dict[str, Any], db: Session) -> dict[str, Any]:
    from app.api import functions as functions_api  # Local import avoids circular import at module load time.

    return asyncio.run(functions_api.invoke_function(function_id, payload, db=db))


def _verify_stats_provider_function(
    *,
    function: models.Function,
    db: Session,
    invoke_payload: dict[str, Any],
) -> dict[str, Any]:
    merged_payload = {
        "payload": {
            "filters": {},
            "limit": 20,
            **(
                invoke_payload.get("payload")
                if isinstance(invoke_payload.get("payload"), dict)
                else {}
            ),
        },
        "runtime_path": "draft",
        "execution_mode": "plan",
        "write_mode": "readonly",
        **{k: v for k, v in invoke_payload.items() if k in {"runtime_path", "execution_mode", "write_mode"}},
    }
    invocation = _invoke_function_sync(int(function.id), merged_payload, db=db)
    if str(invocation.get("status") or "").strip().lower() != "success":
        return {
            "ok": False,
            "code": "dependency_verification_failed",
            "dependency": _STATS_PROVIDER_DEPENDENCY_KEY,
            "function_id": int(function.id or 0),
            "error": str(invocation.get("error_message") or "invoke failed"),
        }
    output = invocation.get("output") if isinstance(invocation.get("output"), dict) else {}
    if not isinstance(output.get("collection_status"), dict):
        return {
            "ok": False,
            "code": "dependency_output_contract_failed",
            "dependency": _STATS_PROVIDER_DEPENDENCY_KEY,
            "function_id": int(function.id or 0),
            "message": "collection_status is required in dependency output",
        }
    return {
        "ok": True,
        "status": str(invocation.get("status") or ""),
        "run_id": str(invocation.get("run_id") or ""),
        "duration_ms": int(invocation.get("duration_ms") or 0),
    }


def _plan_page_dependencies(
    *,
    db: Session,
    orchestration: dict[str, Any],
) -> dict[str, Any]:
    dependencies = orchestration.get("dependencies") if isinstance(orchestration.get("dependencies"), list) else []
    if not dependencies:
        return {
            "dependency_planned": False,
            "bindings": [],
            "dependencies": [],
        }

    planned_dependencies: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            continue
        key = _normalize_dependency_key(dependency.get("key"))
        if key != _STATS_PROVIDER_DEPENDENCY_KEY:
            planned_dependencies.append(
                {
                    "key": key,
                    "planned": False,
                    "reason": "unsupported_dependency",
                }
            )
            continue
        function = _ensure_stats_provider_function(db)
        endpoint = f"/api/v1/functions/{int(function.id)}/invoke"
        verification: dict[str, Any] | None = None
        if bool(dependency.get("verify", True)):
            verification = _verify_stats_provider_function(
                function=function,
                db=db,
                invoke_payload=dependency.get("invoke_payload") if isinstance(dependency.get("invoke_payload"), dict) else {},
            )
            if not bool(verification.get("ok")):
                planned_dependencies.append(
                    {
                        "key": key,
                        "planned": False,
                        "strategy": str(dependency.get("strategy") or "create_or_reuse"),
                        "function_id": int(function.id or 0),
                        "function_name": str(function.name or ""),
                        "endpoint": endpoint,
                        "verification": verification,
                        "reason": str(verification.get("code") or "dependency_verification_failed"),
                    }
                )
                continue
        planned_dependencies.append(
            {
                "key": key,
                "planned": True,
                "strategy": str(dependency.get("strategy") or "create_or_reuse"),
                "function_id": int(function.id or 0),
                "function_name": str(function.name or ""),
                "endpoint": endpoint,
                "verification": verification,
            }
        )
        bindings.append(
            {
                "function": function,
                "endpoint": endpoint,
                "key": key,
            }
        )
    return {
        "dependency_planned": bool(bindings),
        "bindings": bindings,
        "dependencies": planned_dependencies,
    }


def _inject_endpoint_reference(text: str, *, endpoint: str, kind: str) -> str:
    normalized = str(text or "")
    if endpoint in normalized:
        return normalized
    if kind == "html":
        return f"{normalized}\n<!-- stats-provider-endpoint: {endpoint} -->\n"
    return f"{normalized}\n// stats-provider-endpoint: {endpoint}\n"


def _attach_stats_provider_binding(
    draft_payload: dict[str, Any],
    *,
    function: models.Function,
    endpoint: str,
) -> dict[str, Any]:
    payload = deepcopy(draft_payload) if isinstance(draft_payload, dict) else {}
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    dependencies = meta.get("dependencies") if isinstance(meta.get("dependencies"), dict) else {}
    functions = dependencies.get("functions") if isinstance(dependencies.get("functions"), list) else []

    binding = {
        "id": int(function.id or 0),
        "name": str(function.name or ""),
        "slug": str(function.slug or ""),
        "invoke_endpoint": endpoint,
    }
    updated_functions: list[dict[str, Any]] = []
    replaced = False
    for item in functions:
        if not isinstance(item, dict):
            continue
        if int(item.get("id") or 0) == int(function.id or 0):
            updated_functions.append(binding)
            replaced = True
        else:
            updated_functions.append(item)
    if not replaced:
        updated_functions.append(binding)
    dependencies["functions"] = updated_functions
    meta["dependencies"] = dependencies
    payload["meta"] = meta

    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    source["code"] = _inject_endpoint_reference(str(source.get("code") or ""), endpoint=endpoint, kind="tsx")
    runtime["preview_html"] = _inject_endpoint_reference(
        str(runtime.get("preview_html") or ""),
        endpoint=endpoint,
        kind="html",
    )
    payload["source"] = source
    payload["runtime"] = runtime
    return payload


@router.get("")
def list_pages(db: Session = Depends(get_db)):
    pages = db.query(models.Page).order_by(models.Page.updated_at.desc()).all()
    return [_serialize(item) for item in pages]


@router.get("/navigation")
def list_navigation_pages(db: Session = Depends(get_db)):
    pages = (
        db.query(models.Page)
        .filter(models.Page.status != PageState.ARCHIVED.value)
        .order_by(models.Page.updated_at.desc())
        .all()
    )
    workspace = next(
        (page for page in pages if page.status in {PageState.DRAFT.value, PageState.PREVIEWING.value}),
        None,
    )
    published_pages = [page for page in pages if page.status == PageState.PUBLISHED.value]

    result: list[dict[str, Any]] = []
    if workspace is not None:
        result.append(
            {
                "id": workspace.id,
                "name": workspace.name,
                "status": workspace.status,
                "path": f"/page/workspace/{workspace.id}",
                "entry_type": "workspace",
                "current_release_id": workspace.current_release_id,
                "updated_at": str(workspace.updated_at),
            }
        )

    for page in published_pages:
        result.append(
            {
                "id": page.id,
                "name": page.name,
                "status": page.status,
                "path": f"/page/{page.id}",
                "entry_type": "published",
                "current_release_id": page.current_release_id,
                "updated_at": str(page.updated_at),
            }
        )

    return result


@router.post("", status_code=status.HTTP_201_CREATED)
def create_page(payload: dict[str, Any], db: Session = Depends(get_db)):
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    page = models.Page(
        name=name,
        description=payload.get("description"),
        status=PageState.DRAFT.value,
        draft_payload=payload.get("draft_payload"),
    )
    db.add(page)
    db.commit()
    db.refresh(page)
    main_path = WorkspaceStore().sync_page_draft(page)
    page.source_path = str(main_path)
    page.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(page)
    return _serialize(page)


@router.get("/{page_id}")
def get_page(page_id: int, db: Session = Depends(get_db)):
    return _serialize(_get_page_or_404(db, page_id))


@router.patch("/{page_id}")
def update_page(page_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    page = _get_page_or_404(db, page_id)
    mutated = False
    for field in ("name", "description", "draft_payload"):
        if field in payload:
            incoming = payload[field]
            if getattr(page, field) != incoming:
                setattr(page, field, incoming)
                mutated = True
    if mutated and page.status == PageState.PUBLISHED.value:
        # Any edit on published page starts a new draft iteration.
        PageLifecycleService().transition(page, target_state=PageState.DRAFT)
    page.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(page)
    main_path = WorkspaceStore().sync_page_draft(page)
    page.source_path = str(main_path)
    page.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(page)
    return _serialize(page)


@router.post("/{page_id}/build")
def build_page(page_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    # Compatibility path, internally routed to build-runs flow.
    return create_page_build_run(page_id=page_id, payload=payload, db=db)


@router.post("/{page_id}/build-runs")
def create_page_build_run(
    page_id: int,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
):
    effective_payload = dict(payload) if isinstance(payload, dict) else {}
    sink_candidate = effective_payload.pop(_PAGE_STREAM_EVENT_SINK_KEY, None)
    event_sink = sink_candidate if callable(sink_candidate) else None
    page = _get_page_or_404(db, page_id)
    prompt = str(effective_payload.get("prompt") or "").strip()
    conversation_context = str(effective_payload.get("conversation_context") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    if _should_use_legacy_page_builder() or not _should_use_orchestrated_page_builder(effective_payload):
        return _run_legacy_page_builder(
            db=db,
            page=page,
            prompt=prompt,
            event_sink=event_sink,
        )
    orchestration = _normalize_page_orchestration(effective_payload)
    missing_slots = _collect_missing_required_slots(orchestration) if bool(orchestration.get("enabled")) else []
    if missing_slots:
        _emit_page_phase_event(
            event_sink,
            phase="reflect",
            status="blocked",
            summary=f"策略复盘 · 缺少 {len(missing_slots)} 个必填信息，需先澄清",
            payload={"missing_slots": missing_slots},
        )
        clarification_run = _build_page_clarification_run(
            prompt=prompt,
            draft_payload=page.draft_payload if isinstance(page.draft_payload, dict) else {},
            summary=f"还缺少 {len(missing_slots)} 个必填信息，请先补充后再构建。",
            detail_payload={
                "reason": "intake_incomplete",
                "mode": orchestration.get("mode"),
                "scenario_id": orchestration.get("scenario_id"),
                "missing_slots": missing_slots,
                "required_slots": orchestration.get("required_slots") if isinstance(orchestration.get("required_slots"), list) else [],
            },
        )
        run_record = _persist_page_build_run(
            db,
            page=page,
            prompt=prompt,
            run=clarification_run,
        )
        db.commit()
        db.refresh(page)
        db.refresh(run_record)
        return {
            "page": _serialize(page),
            "build_summary": clarification_run.summary,
            "build_run": _serialize_build_run(run_record, with_events=True),
        }
    dependency_plan = _plan_page_dependencies(db=db, orchestration=orchestration) if bool(orchestration.get("enabled")) else {
        "dependency_planned": False,
        "bindings": [],
        "dependencies": [],
    }
    dependency_blockers = [
        item
        for item in (dependency_plan.get("dependencies") or [])
        if isinstance(item, dict) and not bool(item.get("planned"))
    ]
    if dependency_blockers:
        _emit_page_phase_event(
            event_sink,
            phase="reflect",
            status="blocked",
            summary="策略复盘 · 依赖能力未就绪，等待补充",
            payload={"blocked_dependencies": dependency_blockers},
        )
        clarification_run = _build_page_clarification_run(
            prompt=prompt,
            draft_payload=page.draft_payload if isinstance(page.draft_payload, dict) else {},
            summary="依赖能力暂未就绪，已暂停构建，请先确认依赖配置。",
            detail_payload={
                "reason": "dependency_blocked",
                "mode": orchestration.get("mode"),
                "scenario_id": orchestration.get("scenario_id"),
                "blocked_dependencies": dependency_blockers,
            },
        )
        run_record = _persist_page_build_run(
            db,
            page=page,
            prompt=prompt,
            run=clarification_run,
        )
        db.commit()
        db.refresh(page)
        db.refresh(run_record)
        return {
            "page": _serialize(page),
            "build_summary": clarification_run.summary,
            "build_run": _serialize_build_run(run_record, with_events=True),
        }
    dependency_context_lines = [
        f"dependency:{item.get('key')} endpoint={item.get('endpoint')}"
        for item in (dependency_plan.get("dependencies") or [])
        if isinstance(item, dict) and bool(item.get("planned")) and str(item.get("endpoint") or "").strip()
    ]
    effective_context = conversation_context
    if dependency_context_lines:
        effective_context = "\n".join(
            [item for item in (conversation_context, *dependency_context_lines) if str(item or "").strip()]
        )
    logger.info(
        "page_build_mode_selected %s",
        fmt_kv(
            page_id=page.id,
            engine="pi_lite",
            has_context=bool(conversation_context),
            orchestration_enabled=bool(orchestration.get("enabled")),
        ),
    )
    workspace = WorkspaceStore()
    recent_contexts = _load_recent_page_build_contexts(db, page_id=page.id)
    previous_draft_payload = deepcopy(page.draft_payload) if isinstance(page.draft_payload, dict) else {}
    dependency_bindings = [item for item in (dependency_plan.get("bindings") or []) if isinstance(item, dict)]

    def _finalize_draft_payload(current_draft: dict[str, Any], _apply_result: Any) -> dict[str, Any]:
        finalize_summary = (
            str(
                getattr(_apply_result, "assistant_message", "")
                or getattr(_apply_result, "diff_summary", "")
                or "页面草稿已更新。"
            ).strip()
            or "页面草稿已更新。"
        )
        finalized = _append_page_build_history(
            current_draft if isinstance(current_draft, dict) else {},
            prompt=prompt,
            summary=finalize_summary,
        )
        for binding in dependency_bindings:
            function = binding.get("function")
            endpoint = str(binding.get("endpoint") or "").strip()
            if not isinstance(function, models.Function) or not endpoint:
                continue
            finalized = _attach_stats_provider_binding(
                finalized,
                function=function,
                endpoint=endpoint,
            )
        return finalized

    orchestrator = PageBuilderOrchestrator()
    released_functions = db.query(models.Function).filter(models.Function.status == "released").all()
    existing_functions = [
        {"id": fn.id, "name": fn.name, "description": fn.description or ""}
        for fn in released_functions
    ]
    orchestration_result = orchestrator.execute(
        page=page,
        command=PageBuildOrchestratorCommand(
            prompt=prompt,
            conversation_context=effective_context,
            recent_contexts=[item for item in recent_contexts if isinstance(item, dict)],
            orchestration=orchestration,
            dependency_plan={
                "dependency_planned": bool(dependency_plan.get("dependency_planned")),
                "dependencies": dependency_plan.get("dependencies") if isinstance(dependency_plan.get("dependencies"), list) else [],
            },
        ),
        workspace_store=workspace,
        finalize_draft=_finalize_draft_payload,
        event_callback=event_sink,
        existing_functions=existing_functions or None,
    )
    if str(orchestration_result.status) == "needs_clarification":
        page.draft_payload = previous_draft_payload
        clarification_run = _build_page_clarification_run(
            prompt=prompt,
            draft_payload=previous_draft_payload,
            summary=str(orchestration_result.summary or "需要补充信息后继续。"),
            detail_payload={
                "reason": str(orchestration_result.reason or "needs_clarification"),
                "planner": orchestration_result.plan_summary,
                "attempts": orchestration_result.attempts,
                "page_verification": {
                    "passed": bool(orchestration_result.page_verification.passed)
                    if orchestration_result.page_verification is not None
                    else None,
                    "diagnostics": orchestration_result.page_verification.diagnostics
                    if orchestration_result.page_verification is not None
                    else [],
                    "checks": orchestration_result.page_verification.checks
                    if orchestration_result.page_verification is not None
                    else [],
                    "semantic_review": orchestration_result.page_verification.semantic_review
                    if orchestration_result.page_verification is not None
                    else None,
                },
                "e2e_verification": {
                    "passed": bool(orchestration_result.e2e_verification.passed)
                    if orchestration_result.e2e_verification is not None
                    else None,
                    "diagnostics": orchestration_result.e2e_verification.diagnostics
                    if orchestration_result.e2e_verification is not None
                    else [],
                    "checks": orchestration_result.e2e_verification.checks
                    if orchestration_result.e2e_verification is not None
                    else [],
                },
            },
        )
        run_record = _persist_page_build_run(
            db,
            page=page,
            prompt=prompt,
            run=clarification_run,
        )
        db.commit()
        db.refresh(page)
        db.refresh(run_record)
        return {
            "page": _serialize(page),
            "build_summary": clarification_run.summary,
            "build_run": _serialize_build_run(run_record, with_events=True),
        }
    result = orchestration_result.apply_result
    if result is None:
        raise HTTPException(status_code=500, detail="page build orchestrator returned no apply_result")
    next_draft_payload = (
        deepcopy(orchestration_result.next_draft_payload)
        if isinstance(orchestration_result.next_draft_payload, dict)
        else {}
    )
    summary_text = str(orchestration_result.summary or "页面草稿已更新。")
    orchestration_payload: dict[str, Any] = {
        "enabled": bool(orchestration.get("enabled")),
        "mode": str(orchestration.get("mode") or "legacy"),
        "scenario_id": str(orchestration.get("scenario_id") or ""),
        "dependency_planned": bool(dependency_plan.get("dependency_planned")),
        "dependencies": dependency_plan.get("dependencies") if isinstance(dependency_plan.get("dependencies"), list) else [],
    }
    build_run = PageBuildRunResult(
        run_id=f"pbr_{uuid4().hex[:16]}",
        status="done",
        phase="apply",
        summary=summary_text,
        draft_payload=next_draft_payload,
        events=[
            PageBuildRunEvent(
                phase="apply",
                status="done",
                summary=summary_text,
                created_at=_utc_now_naive().isoformat(),
                payload={
                    "changed_files": result.changed_files,
                    "diff_summary": result.diff_summary,
                    "assistant_message": result.assistant_message,
                    "tests_suggested": result.tests_suggested,
                    "risk_notes": result.risk_notes,
                    "engine": "pi_lite",
                    "planner": orchestration_result.plan_summary,
                    "attempts": orchestration_result.attempts,
                    "verification": {
                        "page": {
                            "passed": bool(orchestration_result.page_verification.passed)
                            if orchestration_result.page_verification is not None
                            else False,
                            "diagnostic_count": len(orchestration_result.page_verification.diagnostics)
                            if orchestration_result.page_verification is not None
                            else 0,
                            "checks": orchestration_result.page_verification.checks
                            if orchestration_result.page_verification is not None
                            else [],
                            "semantic_review": orchestration_result.page_verification.semantic_review
                            if orchestration_result.page_verification is not None
                            else None,
                        },
                        "e2e": {
                            "passed": bool(orchestration_result.e2e_verification.passed)
                            if orchestration_result.e2e_verification is not None
                            else False,
                            "diagnostic_count": len(orchestration_result.e2e_verification.diagnostics)
                            if orchestration_result.e2e_verification is not None
                            else 0,
                            "checks": orchestration_result.e2e_verification.checks
                            if orchestration_result.e2e_verification is not None
                            else [],
                        },
                    },
                    "orchestration": orchestration_payload,
                },
            )
        ],
        error_summary=None,
    )
    page.draft_payload = next_draft_payload
    if build_run.status == "done" and page.status == PageState.PUBLISHED.value:
        # Editing a published page creates a new draft iteration for the next release.
        PageLifecycleService().transition(page, target_state=PageState.DRAFT)
    page.updated_at = _utc_now_naive()
    run_record = _persist_page_build_run(
        db,
        page=page,
        prompt=prompt,
        run=build_run,
    )
    db.commit()
    db.refresh(page)
    db.refresh(run_record)
    main_path = WorkspaceStore().sync_page_draft(page)
    page.source_path = str(main_path)
    page.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(page)
    logger.info(
        "page_build_run %s",
        fmt_kv(
            page_id=page.id,
            run_id=run_record.run_id,
            status=run_record.status,
            phase=run_record.phase,
        ),
    )
    return {
        "page": _serialize(page),
        "build_summary": build_run.summary,
        "build_run": _serialize_build_run(run_record, with_events=True),
    }


@router.post("/{page_id}/build-runs/stream")
async def create_page_build_run_stream(page_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    _get_page_or_404(db, page_id)
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
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
    trace_id = str(uuid4())
    sequence = 0
    emitted_core_names: set[str] = set()

    def enqueue(event: dict[str, Any]) -> None:
        nonlocal sequence
        event_type = str(event.get("type") or "").strip().lower()
        if event_type == "phase":
            event_group = str(event.get("event_group") or "").strip().lower()
            event_name = str(event.get("event_name") or event.get("phase") or "").strip().lower()
            if (not event_group or event_group == "core") and event_name:
                emitted_core_names.add(event_name)
        sequence += 1
        envelope = {
            "id": f"{trace_id}:{sequence}",
            "seq": sequence,
            **event,
        }
        loop.call_soon_threadsafe(queue.put_nowait, envelope)

    emitted_phase_events = 0

    def phase_sink(event: dict[str, Any]) -> None:
        nonlocal emitted_phase_events
        phase = str(event.get("phase") or "").strip()
        if not phase:
            return
        status_text = str(event.get("status") or "").strip()
        summary_text = str(event.get("summary") or "").strip()
        payload_data = event.get("payload") if isinstance(event.get("payload"), dict) else None
        created_at = str(event.get("created_at") or "")
        enqueue(
            _to_page_stream_core_event(
                phase=phase,
                status=status_text,
                summary=summary_text,
                payload=payload_data,
                created_at=created_at,
            )
        )
        emitted_phase_events += 1

    def worker() -> None:
        local_db = runtime_session_factory()
        try:
            stream_payload = dict(payload)
            stream_payload[_PAGE_STREAM_EVENT_SINK_KEY] = phase_sink
            try:
                result = create_page_build_run(page_id=page_id, payload=stream_payload, db=local_db)
            except TypeError:
                # Backward-compatible for monkeypatched call sites that still use legacy signature.
                result = create_page_build_run(page_id=page_id, payload=payload, db=local_db)
            if not isinstance(result, dict):
                raise ValueError("page build stream result is invalid")
            if emitted_phase_events == 0:
                # Backward compatibility fallback for non-stream-aware call paths.
                for event in _derive_page_stream_events(result):
                    enqueue(event)
            if "plan" not in emitted_core_names:
                enqueue(
                    _to_page_stream_core_event(
                        phase="plan",
                        status="done",
                        summary="需求规划 · 已接收构建请求",
                        payload={"fallback": True},
                    )
                )
            build_run = result.get("build_run") if isinstance(result.get("build_run"), dict) else {}
            assistant_message = str(
                build_run.get("result_summary") or build_run.get("error_summary") or result.get("build_summary") or ""
            ).strip()
            events = build_run.get("events") if isinstance(build_run.get("events"), list) else []
            extension_emitted = {"verify_result": False, "preview_result": False}
            for item in events:
                if not isinstance(item, dict):
                    continue
                phase = str(item.get("phase") or "").strip()
                payload_data = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                verification = payload_data.get("verification") if isinstance(payload_data.get("verification"), dict) else None
                if verification is not None and not extension_emitted["verify_result"]:
                    verify_passed = bool(
                        (verification.get("page") or {}).get("passed")
                        if isinstance(verification.get("page"), dict)
                        else verification.get("passed")
                    )
                    enqueue(
                        {
                            "type": "extension",
                            "event_group": "extension",
                            "event_name": "verify_result",
                            "data": {
                                "summary": "业务结果校验通过" if verify_passed else "业务结果校验未通过",
                                "verification": verification,
                                "source": "verifier",
                                "agent": "PageVerifier",
                            },
                        }
                    )
                    extension_emitted["verify_result"] = True
                if phase in {"apply", "preview_ready"} and not extension_emitted["preview_result"]:
                    page_payload = result.get("page") if isinstance(result.get("page"), dict) else {}
                    draft = page_payload.get("draft_payload") if isinstance(page_payload.get("draft_payload"), dict) else {}
                    config = draft.get("config") if isinstance(draft.get("config"), dict) else {}
                    runtime = draft.get("runtime") if isinstance(draft.get("runtime"), dict) else {}
                    enqueue(
                        {
                            "type": "extension",
                            "event_group": "extension",
                            "event_name": "preview_result",
                            "data": {
                                "summary": "Preview · 预览已刷新",
                                "title": str(config.get("title") or ""),
                                "has_preview_html": bool(str(runtime.get("preview_html") or "").strip()),
                                "source": "runtime",
                                "agent": "PageBuilderAgent",
                            },
                        }
                    )
                    extension_emitted["preview_result"] = True
            if assistant_message:
                enqueue(
                    {
                        "type": "assistant",
                        "event_group": "core",
                        "event_name": "assistant",
                        "phase": "responding",
                        "data": {"text": assistant_message, "source": "llm", "agent": "PageBuilderAgent"},
                    }
                )
            enqueue(
                {
                    "type": "done",
                    "event_group": "core",
                    "event_name": "done",
                    "data": {
                        "trace_id": trace_id,
                        "scope": "page.build",
                        "action": "build",
                        "status": str(build_run.get("status") or "done"),
                        "assistant_message": assistant_message,
                        "source": "runtime",
                        "agent": "PageBuilderOrchestrator",
                        "page": result.get("page"),
                        "build_summary": result.get("build_summary"),
                        "build_run": build_run,
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
                        "agent": "PageBuilderOrchestrator",
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
                        "agent": "PageBuilderOrchestrator",
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


@router.get("/{page_id}/build-runs")
def list_page_build_runs(page_id: int, limit: int = 20, db: Session = Depends(get_db)):
    _get_page_or_404(db, page_id)
    rows = (
        db.query(models.PageBuildRun)
        .filter(models.PageBuildRun.page_id == page_id)
        .order_by(models.PageBuildRun.created_at.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return [_serialize_build_run(item) for item in rows]


@router.get("/{page_id}/build-runs/{run_id}")
def get_page_build_run(page_id: int, run_id: str, db: Session = Depends(get_db)):
    _get_page_or_404(db, page_id)
    run = _get_page_build_run_or_404(db, page_id=page_id, run_id=run_id)
    return _serialize_build_run(run)


@router.get("/{page_id}/build-runs/{run_id}/events")
def list_page_build_run_events(page_id: int, run_id: str, db: Session = Depends(get_db)):
    _get_page_or_404(db, page_id)
    run = _get_page_build_run_or_404(db, page_id=page_id, run_id=run_id)
    events = (
        db.query(models.PageBuildEvent)
        .filter(models.PageBuildEvent.build_run_id == run.id)
        .order_by(models.PageBuildEvent.created_at.asc())
        .all()
    )
    return [_serialize(item) for item in events]


@router.delete("/{page_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_page(page_id: int, db: Session = Depends(get_db)):
    page = _get_page_or_404(db, page_id)
    db.delete(page)
    db.commit()
    return None


@router.get("/{page_id}/releases")
def list_page_releases(page_id: int, db: Session = Depends(get_db)):
    _get_page_or_404(db, page_id)
    releases = (
        db.query(models.PageRelease)
        .filter(models.PageRelease.page_id == page_id)
        .order_by(models.PageRelease.version.desc())
        .all()
    )
    return [_serialize(item) for item in releases]


@router.post("/{page_id}/preview")
def preview_page(page_id: int, db: Session = Depends(get_db)):
    lifecycle = PageLifecycleService()
    page = _get_page_or_404(db, page_id)
    try:
        lifecycle.transition(page, target_state=PageState.PREVIEWING)
    except LifecycleValidationError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    page.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(page)
    return _serialize(page)


@router.post("/{page_id}/publish")
def publish_page(page_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    lifecycle = PageLifecycleService()
    page = _get_page_or_404(db, page_id)
    artifact_payload = payload.get("artifact_payload")
    if artifact_payload is None:
        latest_compile = (
            db.query(models.PageCompileRun)
            .filter(
                models.PageCompileRun.page_id == page_id,
                models.PageCompileRun.status == "done",
            )
            .order_by(models.PageCompileRun.created_at.desc())
            .first()
        )
        if latest_compile is None or not isinstance(latest_compile.artifact_payload, dict):
            raise HTTPException(
                status_code=400,
                detail="compile is required before publish when artifact_payload is not provided",
            )
        artifact_payload = latest_compile.artifact_payload
    try:
        release = lifecycle.publish(
            page,
            artifact_payload=artifact_payload or {},
            artifact_uri=payload.get("artifact_uri"),
            release_notes=payload.get("release_notes"),
        )
    except LifecycleValidationError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    page.updated_at = _utc_now_naive()
    main_path = WorkspaceStore().sync_page_draft(page)
    page.source_path = str(main_path)
    commit_sha = WorkspaceStore().commit_publish(
        object_type="page",
        object_id=page.id,
        action="publish",
        summary=str(page.name or ""),
    )
    page.current_commit_sha = commit_sha
    page.release_commit_sha = commit_sha
    db.commit()
    db.refresh(page)
    db.refresh(release)
    return {
        "page": _serialize(page),
        "release": _serialize(release),
    }


@router.post("/{page_id}/freeze")
def freeze_page_draft(page_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    page = _get_page_or_404(db, page_id)
    draft_payload = page.draft_payload if isinstance(page.draft_payload, dict) else {}
    snapshot = models.PageDraftSnapshot(
        page_id=page.id,
        summary=str(payload.get("summary") or draft_payload.get("meta", {}).get("summary") or ""),
        snapshot_payload=draft_payload,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return _serialize(snapshot)


@router.get("/{page_id}/snapshots")
def list_page_snapshots(page_id: int, limit: int = 20, db: Session = Depends(get_db)):
    _get_page_or_404(db, page_id)
    snapshots = (
        db.query(models.PageDraftSnapshot)
        .filter(models.PageDraftSnapshot.page_id == page_id)
        .order_by(models.PageDraftSnapshot.created_at.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return [_serialize(item) for item in snapshots]


@router.post("/{page_id}/compile")
def compile_page(page_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    page = _get_page_or_404(db, page_id)
    snapshot_id = payload.get("snapshot_id")
    snapshot = None
    if isinstance(snapshot_id, int):
        snapshot = (
            db.query(models.PageDraftSnapshot)
            .filter(
                models.PageDraftSnapshot.page_id == page_id,
                models.PageDraftSnapshot.id == snapshot_id,
            )
            .first()
        )
    if snapshot is None:
        snapshot = (
            db.query(models.PageDraftSnapshot)
            .filter(models.PageDraftSnapshot.page_id == page_id)
            .order_by(models.PageDraftSnapshot.created_at.desc())
            .first()
        )
    if snapshot is None:
        raise HTTPException(status_code=400, detail="snapshot is required before compile")

    run = models.PageCompileRun(
        run_id=f"pcr_{uuid4().hex[:16]}",
        page_id=page.id,
        snapshot_id=snapshot.id,
        status="running",
    )
    db.add(run)
    db.flush()
    try:
        artifact_payload, summary = _compile_page_artifact(snapshot.snapshot_payload)
        run.status = "done"
        run.summary = summary
        run.artifact_payload = artifact_payload
        run.finished_at = _utc_now_naive()
    except Exception as err:
        run.status = "failed"
        run.error_summary = str(err)
        run.finished_at = _utc_now_naive()
    db.commit()
    db.refresh(run)
    return _serialize_compile_run(run)


@router.get("/{page_id}/compile-runs")
def list_page_compile_runs(page_id: int, limit: int = 20, db: Session = Depends(get_db)):
    _get_page_or_404(db, page_id)
    runs = (
        db.query(models.PageCompileRun)
        .filter(models.PageCompileRun.page_id == page_id)
        .order_by(models.PageCompileRun.created_at.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return [_serialize_compile_run(item) for item in runs]


@router.get("/{page_id}/published")
def get_published_page(page_id: int, db: Session = Depends(get_db)):
    page = _get_page_or_404(db, page_id)
    if page.status != PageState.PUBLISHED.value or page.current_release is None:
        raise HTTPException(status_code=404, detail="Published page not found")
    return {
        "page": {
            "id": page.id,
            "name": page.name,
            "status": page.status,
            "updated_at": str(page.updated_at),
        },
        "release": _serialize(page.current_release),
    }


@router.post("/{page_id}/archive")
def archive_page(page_id: int, db: Session = Depends(get_db)):
    lifecycle = PageLifecycleService()
    page = _get_page_or_404(db, page_id)
    try:
        lifecycle.archive(page)
    except LifecycleValidationError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    page.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(page)
    return _serialize(page)


@router.post("/{page_id}/rollback")
def rollback_page(page_id: int, payload: dict[str, Any], db: Session = Depends(get_db)):
    lifecycle = PageLifecycleService()
    page = _get_page_or_404(db, page_id)
    release_id = payload.get("release_id")
    if not isinstance(release_id, int):
        raise HTTPException(status_code=400, detail="release_id is required")
    try:
        release = lifecycle.rollback(page, target_release_id=release_id)
    except LifecycleValidationError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    page.updated_at = _utc_now_naive()
    db.commit()
    db.refresh(page)
    db.refresh(release)
    return {
        "page": _serialize(page),
        "release": _serialize(release),
    }
