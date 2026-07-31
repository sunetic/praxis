from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.connection import get_db_pool
from app.db.database import SessionLocal, get_db
from app.models import models
from app.services.llm import get_llm_client
from app.services.scheduler.runtime_state import get_scheduler_worker

router = APIRouter(prefix="/stats-analysis", tags=["StatsAnalysis"])
logger = get_logger("app.api.stats_analysis")


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class StatsTaskSummary(BaseModel):
    total_tasks: int
    success_tasks: int
    failed_tasks: int
    failed_task_ratio_pct: float
    total_tables_planned: int
    total_tables_failed: int


class StatsSchedulerWindow(BaseModel):
    job_name: str
    enabled: bool
    last_start_date: str | None = None
    next_run_date: str | None = None
    failure_count: int | None = None
    datasource_id: int | None = None
    cluster_key: str | None = None


class StatsOverviewResponse(BaseModel):
    task_summary: StatsTaskSummary
    scheduler_windows: list[StatsSchedulerWindow]


class StatsFailedTableItem(BaseModel):
    tenant_name: str | None = None
    owner: str | None = None
    table_name: str | None = None
    task_start_time: str | None = None
    task_end_time: str | None = None
    gather_seconds: int | None = None
    memory_used: int | None = None
    stat_refresh_failed_list: str | None = None
    status: str | None = None
    datasource_id: int | None = None
    cluster_key: str | None = None


class StatsFailedTablesResponse(BaseModel):
    items: list[StatsFailedTableItem]


class StatsStaleTableItem(BaseModel):
    tenant_name: str | None = None
    owner: str | None = None
    table_name: str | None = None
    last_analyzed: str | None = None
    stats_state: str | None = None
    datasource_id: int | None = None
    cluster_key: str | None = None


class StatsStaleTablesResponse(BaseModel):
    items: list[StatsStaleTableItem]


class StatsDmlChangeItem(BaseModel):
    tenant_name: str | None = None
    database_name: str | None = None
    table_name: str | None = None
    row_change_delta: int | None = None
    datasource_id: int | None = None
    cluster_key: str | None = None


class StatsDmlChangesResponse(BaseModel):
    items: list[StatsDmlChangeItem]


class StatsColStatItem(BaseModel):
    owner: str | None = None
    table_name: str | None = None
    column_name: str | None = None
    num_distinct: int | None = None
    num_buckets: int | None = None
    histogram: str | None = None
    sample_size: int | None = None
    last_analyzed: str | None = None


class StatsColStatsResponse(BaseModel):
    items: list[StatsColStatItem]


class StatsHistogramItem(BaseModel):
    owner: str | None = None
    table_name: str | None = None
    column_name: str | None = None
    bucket_cnt: int | None = None
    max_bucket_repeat: int | None = None
    total_repeat: int | None = None
    top_bucket_ratio: float | None = None


class StatsHistogramResponse(BaseModel):
    items: list[StatsHistogramItem]


class StatsTrendPoint(BaseModel):
    date: str  # YYYY-MM-DD
    avg_duration_min: float  # 平均任务耗时（分钟）
    max_duration_min: float  # 最大任务耗时（分钟）
    failed_tables: int  # 当天失败表总数
    total_tasks: int  # 当天任务数


class StatsTrendResponse(BaseModel):
    points: list[StatsTrendPoint]


class StatsCollectionDaySummary(BaseModel):
    date: str
    task_type: str
    total_tasks: int
    success_tasks: int
    failed_tasks: int
    total_tables: int
    success_tables: int
    failed_tables: int
    avg_duration_min: float
    max_duration_min: float
    cluster_key: str | None = None
    tenant_name: str | None = None
    datasource_id: int | None = None


class StatsCollectionDailySummaryResponse(BaseModel):
    datasource_id: int | None = None
    items: list[StatsCollectionDaySummary]


class StatsDailyTaskItem(BaseModel):
    task_id: str | None = None
    task_type: str | None = None
    status: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    duration_seconds: int | None = None
    table_count: int | None = None
    failed_count: int | None = None
    cluster_key: str | None = None
    tenant_name: str | None = None
    datasource_id: int | None = None


class StatsDailyTasksResponse(BaseModel):
    datasource_id: int | None = None
    date: str
    items: list[StatsDailyTaskItem]
    total: int = 0
    page: int = 1
    page_size: int = 50


class StatsDailyFailedTableItem(BaseModel):
    owner: str | None = None
    table_name: str | None = None
    failure_count: int = 1
    latest_status: str | None = None
    latest_error: str | None = None
    latest_gather_seconds: int | None = None
    latest_task_start_time: str | None = None
    cluster_key: str | None = None
    tenant_name: str | None = None
    datasource_id: int | None = None


class StatsDailyFailedTablesResponse(BaseModel):
    datasource_id: int | None = None
    date: str
    items: list[StatsDailyFailedTableItem]


class StatsWorkbenchCard(BaseModel):
    key: str
    title: str
    value: str
    status: Literal["healthy", "warning", "critical", "info"]
    hint: str | None = None


class StatsIssueItem(BaseModel):
    issue_id: str
    kind: Literal["scheduling", "failed_table", "stale_stats", "dml_change"]
    severity: Literal["high", "medium", "low"]
    title: str
    summary: str
    datasource_id: int | None = None
    cluster_key: str | None = None
    tenant_name: str | None = None
    database_name: str | None = None
    table_name: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)


class StatsTenantConfigCheck(BaseModel):
    tenant_name: str
    datasource_id: int
    auto_gather_enabled: bool | None = None
    enabled_windows: int = 0
    total_windows: int = 7
    recent_task_count: int = 0
    issue_type: Literal[
        "auto_gather_disabled",
        "no_windows",
        "partial_windows",
        "no_recent_tasks",
        "unreachable",
        "healthy",
    ]
    issue_label: str
    suggestion_sql: str


class StatsWorkbenchResponse(BaseModel):
    datasource_id: int | None = None
    cluster_key: str
    overview: StatsOverviewResponse
    cards: list[StatsWorkbenchCard]
    issues: list[StatsIssueItem]
    warnings: list[str] = Field(default_factory=list)
    tenant_config_checks: list[StatsTenantConfigCheck] = Field(default_factory=list)


class StatsDiagnosisEvidence(BaseModel):
    label: str
    value: str
    source: str | None = None


class StatsDiagnosisAction(BaseModel):
    title: str
    rationale: str | None = None
    risk: str | None = None
    execution_window: str | None = None


class StatsDiagnosisResult(BaseModel):
    headline: str
    verdict: str
    reasoning: str
    evidence: list[StatsDiagnosisEvidence] = Field(default_factory=list)
    next_actions: list[StatsDiagnosisAction] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    diagnosis_path: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class StatsDiagnosisRequest(BaseModel):
    datasource_id: int
    mode: Literal["summary", "deep"] = "summary"
    issue: StatsIssueItem


class StatsDiagnosisTaskSubmitResponse(BaseModel):
    task_id: str
    status: Literal["pending", "running", "ready", "degraded", "needs_clarification", "error"]


class StatsDiagnosisTaskStatusResponse(BaseModel):
    task_id: str
    status: Literal["pending", "running", "ready", "degraded", "needs_clarification", "error"]
    result: StatsDiagnosisResult | None = None


class StatsRiskCandidateTagItem(BaseModel):
    tag_key: str
    tag_label: str
    severity: Literal["high", "medium", "low"]
    score: float
    facts: dict[str, Any] = Field(default_factory=dict)


class StatsRiskCandidateItem(BaseModel):
    candidate_id: int
    datasource_id: int
    cluster_key: str | None = None
    tenant_name: str | None = None
    database_name: str
    table_name: str
    severity: Literal["high", "medium", "low"]
    score: float
    lifecycle_status: Literal["active", "expired", "resolved"]
    source: str | None = None
    latest_summary: str | None = None
    last_seen_at: str
    tags: list[StatsRiskCandidateTagItem] = Field(default_factory=list)


class StatsRiskCandidatesResponse(BaseModel):
    datasource_id: int
    items: list[StatsRiskCandidateItem]


class StatsRiskCollectRequest(BaseModel):
    datasource_id: int
    lookback_days: int = Field(default=7, ge=1, le=90)
    stale_days: int = Field(default=7, ge=1, le=365)


class StatsRiskCollectResponse(BaseModel):
    datasource_id: int
    collected_tables: int
    active_candidates: int
    expired_candidates: int


class StatsRiskAnalyzeSubmitResponse(BaseModel):
    run_id: str
    status: Literal["pending", "running", "ready", "degraded", "needs_clarification", "error"]


class StatsRiskAnalyzeStatusResponse(BaseModel):
    run_id: str
    status: Literal["pending", "running", "ready", "degraded", "needs_clarification", "error"]
    result: StatsDiagnosisResult | None = None
    error_summary: str | None = None


class StatsRiskCollectionRunItem(BaseModel):
    run_id: str
    datasource_id: int
    trigger_type: str
    status: str
    summary: str | None = None
    error_summary: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class StatsRiskCollectionRunsResponse(BaseModel):
    datasource_id: int
    items: list[StatsRiskCollectionRunItem]


class StatsDrawerDetailField(BaseModel):
    label: str
    value: str
    source: str | None = None


class StatsDrawerDetailSection(BaseModel):
    key: str
    title: str
    description: str | None = None
    fields: list[StatsDrawerDetailField] = Field(default_factory=list)


class StatsDrawerHistoryRow(BaseModel):
    task_id: str | None = None
    owner: str | None = None
    table_name: str | None = None
    status: str | None = None
    ret_code: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    gather_seconds: int | None = None
    memory_used: int | None = None
    trigger_type: str | None = None
    stat_refresh_failed_list: str | None = None
    properties: str | None = None
    task_table_count: int | None = None
    task_failed_count: int | None = None


class StatsDrawerDetailRequest(BaseModel):
    datasource_id: int
    issue: StatsIssueItem | None = None
    risk_candidate: StatsRiskCandidateItem | None = None


class StatsDrawerDetailResponse(BaseModel):
    datasource_id: int
    title: str
    object_kind: str
    severity: Literal["high", "medium", "low"]
    summary: str
    subtitle: str | None = None
    sections: list[StatsDrawerDetailSection] = Field(default_factory=list)
    history_rows: list[StatsDrawerHistoryRow] = Field(default_factory=list)
    history_source: str | None = None
    missing_facts: list[str] = Field(default_factory=list)
    chat_context: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_datasource(db: Session, datasource_id: int) -> models.DataSource:
    ds = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="DataSource not found")
    return ds


def _resolve_datasources(
    db: Session,
    datasource_id: int | None,
    cluster_key: str | None,
) -> list[models.DataSource]:
    """Resolve target datasources for aggregation queries.

    - datasource_id set → single datasource
    - cluster_key set → all active non-sys datasources in that cluster + the active sys datasource
    - both None → all active non-sys datasources across all clusters + active sys datasources
    """
    if datasource_id:
        ds = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
        if not ds:
            raise HTTPException(status_code=404, detail="DataSource not found")
        return [ds]

    rows = (
        db.query(models.DataSource)
        .filter(models.DataSource.status == "active")
        .order_by(models.DataSource.id.asc())
        .all()
    )
    if cluster_key:
        rows = [item for item in rows if item.cluster_key == cluster_key]
    return rows


def _datasource_tenant_name(ds: models.DataSource) -> str | None:
    text = str(ds.name or "").strip()
    return text or None


def _is_sys_datasource(ds: models.DataSource) -> bool:
    role = str(ds.tenant_role or "").strip().lower()
    return role == "sys"


def _count_active_risk_candidates(db: Session, datasource_id: int) -> int:
    return int(
        db.query(models.StatsRiskCandidate)
        .filter(
            models.StatsRiskCandidate.datasource_id == datasource_id,
            models.StatsRiskCandidate.lifecycle_status == "active",
        )
        .count()
    )


def _rows_to_dicts(result: dict) -> list[dict]:
    rows = result.get("rows", [])
    return [dict(r) for r in rows]


def _val(row: dict[str, Any], key: str, default: Any = None) -> Any:
    return row.get(key, default)


def _str(row: dict, key: str) -> str | None:
    v = row.get(key)
    if v is None:
        return None
    return str(v)


def _int(row: dict, key: str) -> int | None:
    v = row.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(row: dict, key: str) -> float | None:
    v = row.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool_enabled(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v != 0
    if isinstance(v, str):
        return v.upper() in ("TRUE", "1", "YES", "ENABLED")
    return False


_DIAG_TASKS: dict[str, dict[str, Any]] = {}
_DIAG_TASKS_LOCK = asyncio.Lock()
_SEVERITY_SCORE = {"high": 3, "medium": 2, "low": 1}
_TERMINAL_DIAG_STATUS = {"ready", "degraded", "needs_clarification", "error"}
_RISK_CANDIDATE_RETENTION_DAYS = 30
_RISK_CANDIDATE_EXPIRE_DAYS = 14
_STATS_ANALYSIS_SCHEDULE_NAME = "stats-analysis-collect"
_STATS_ANALYSIS_SCHEDULE_CRON = "0 2 * * *"


def _safe_text(value: Any, default: str = "-") -> str:
    text = str(value or "").strip()
    return text if text else default


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


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize_json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_json_value(v) for v in value]
    if isinstance(value, tuple):
        return [_normalize_json_value(v) for v in value]
    return value


def _issue_sort_key(issue: StatsIssueItem) -> tuple[int, str]:
    return (_SEVERITY_SCORE.get(issue.severity, 0), issue.issue_id)


def _extract_issue_facts(issue: StatsIssueItem) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in (issue.facts or {}).items():
        normalized[str(key)] = _safe_text(value)
    return normalized


def _detect_verdict(issue: StatsIssueItem) -> tuple[str, str]:
    facts = _extract_issue_facts(issue)
    if issue.kind == "scheduling":
        return "scheduling_window_abnormal", "调度窗口存在禁用或失败记录。"
    if issue.kind == "dml_change":
        return "stale_risk_from_dml", "数据变化量超过阈值，统计信息可能过期。"
    if issue.kind == "stale_stats":
        return "stale_or_missing_stats", "表统计信息缺失或过期，可能导致估行偏差。"
    reason = (facts.get("error_reason", "") or "").lower()
    gather_seconds = int(facts.get("gather_seconds", "0") or "0")
    if "timeout" in reason or gather_seconds >= 1800:
        return "large_table_timeout", "失败特征更接近大表收集超时。"
    if "window" in reason or "time limit" in reason:
        return "window_insufficient", "失败特征更接近收集窗口不足。"
    return "collection_failure", "当前失败原因不满足超时/窗口特征，先按通用收集失败处理。"


def _merge_missing_facts(*groups: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            value = _safe_text(item, "")
            if not value or value in seen:
                continue
            ordered.append(value)
            seen.add(value)
    return ordered


def _merge_evidence(
    base: list[StatsDiagnosisEvidence],
    extra: list[StatsDiagnosisEvidence],
) -> list[StatsDiagnosisEvidence]:
    merged: list[StatsDiagnosisEvidence] = []
    seen: set[tuple[str, str, str | None]] = set()
    for item in [*base, *extra]:
        key = (item.label, item.value, item.source)
        if key in seen:
            continue
        merged.append(item)
        seen.add(key)
    return merged


def _rows_to_plain_dicts(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = (result or {}).get("rows", [])
    return [dict(row) for row in rows]


def _dt_to_iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.replace(microsecond=0).isoformat() + "Z"


def _issue_to_candidate_tags(issue: StatsIssueItem) -> list[dict[str, Any]]:
    tags: list[dict[str, Any]] = []
    facts = issue.facts or {}
    if issue.kind == "failed_table":
        tags.append(
            {
                "tag_key": "collect_failed",
                "tag_label": "收集失败",
                "severity": issue.severity,
                "score": 90.0 if issue.severity == "high" else 70.0,
                "facts": facts,
            }
        )
        gather_seconds = int(facts.get("gather_seconds") or 0)
        if gather_seconds >= 1200:
            tags.append(
                {
                    "tag_key": "long_gather_window",
                    "tag_label": "收集耗时偏长",
                    "severity": "high" if gather_seconds >= 1800 else "medium",
                    "score": 85.0 if gather_seconds >= 1800 else 65.0,
                    "facts": {"gather_seconds": gather_seconds},
                }
            )
    elif issue.kind == "stale_stats":
        state = str(facts.get("stats_state") or "").upper()
        tags.append(
            {
                "tag_key": "stale_stats",
                "tag_label": "统计信息过期",
                "severity": "high" if state == "MISSING_STATS" else issue.severity,
                "score": 80.0 if state == "MISSING_STATS" else 60.0,
                "facts": facts,
            }
        )
    elif issue.kind == "dml_change":
        delta = int(facts.get("row_change_delta") or 0)
        tags.append(
            {
                "tag_key": "high_dml_change",
                "tag_label": "数据变化显著",
                "severity": "high" if delta >= 1_000_000 else issue.severity,
                "score": 88.0 if delta >= 1_000_000 else 62.0,
                "facts": facts,
            }
        )
    return tags


def _compute_candidate_rank(tags: list[dict[str, Any]]) -> tuple[str, float]:
    if not tags:
        return "low", 0.0
    severity = max(
        (str(tag.get("severity") or "low") for tag in tags),
        key=lambda value: _SEVERITY_SCORE.get(value, 0),
    )
    score = max(float(tag.get("score") or 0.0) for tag in tags)
    return severity, round(score, 2)


def _issue_to_candidate_key(issue: StatsIssueItem) -> tuple[str, str]:
    facts = issue.facts or {}
    fallback_db = facts.get("owner") if issue.kind == "failed_table" else None
    database_name = _safe_text(issue.database_name or fallback_db or issue.tenant_name, "unknown")
    table_name = _safe_text(issue.table_name, "unknown")
    return database_name, table_name


def _collect_candidate_rows_from_issues(issues: list[StatsIssueItem]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for issue in issues:
        if issue.kind not in {"failed_table", "stale_stats", "dml_change"}:
            continue
        db_name, table_name = _issue_to_candidate_key(issue)
        key = (db_name, table_name)
        tags = _issue_to_candidate_tags(issue)
        if not tags:
            continue
        current = merged.get(key)
        if current is None:
            merged[key] = {
                "tenant_name": issue.tenant_name,
                "database_name": db_name,
                "table_name": table_name,
                "source": issue.kind,
                "latest_summary": issue.summary,
                "tags": tags,
            }
            continue
        existing_tags: dict[str, dict[str, Any]] = {
            str(tag["tag_key"]): tag for tag in current["tags"]
        }
        for tag in tags:
            existing_tags[str(tag["tag_key"])] = tag
        current["tags"] = list(existing_tags.values())
        if _SEVERITY_SCORE.get(issue.severity, 0) >= _SEVERITY_SCORE.get(
            str(current.get("severity") or "low"), 0
        ):
            current["latest_summary"] = issue.summary
            current["source"] = issue.kind
    return list(merged.values())


def _cleanup_risk_candidates(db: Session, datasource_id: int, *, now: datetime) -> tuple[int, int]:
    expired_cutoff = now - timedelta(days=_RISK_CANDIDATE_EXPIRE_DAYS)
    deleted_cutoff = now - timedelta(days=_RISK_CANDIDATE_RETENTION_DAYS)
    expired = (
        db.query(models.StatsRiskCandidate)
        .filter(
            models.StatsRiskCandidate.datasource_id == datasource_id,
            models.StatsRiskCandidate.lifecycle_status == "active",
            models.StatsRiskCandidate.last_seen_at < expired_cutoff,
        )
        .all()
    )
    for candidate in expired:
        candidate.lifecycle_status = "expired"
    deleted = (
        db.query(models.StatsRiskCandidate)
        .filter(
            models.StatsRiskCandidate.datasource_id == datasource_id,
            models.StatsRiskCandidate.lifecycle_status == "expired",
            models.StatsRiskCandidate.last_seen_at < deleted_cutoff,
        )
        .delete(synchronize_session=False)
    )
    return len(expired), int(deleted)


def _upsert_risk_candidates(
    db: Session,
    ds: models.DataSource,
    candidate_rows: list[dict[str, Any]],
) -> int:
    now = datetime.utcnow()
    seen_candidate_ids: set[int] = set()
    for row in candidate_rows:
        database_name = _safe_text(row.get("database_name"), "unknown")
        table_name = _safe_text(row.get("table_name"), "unknown")
        tags = list(row.get("tags") or [])
        severity, score = _compute_candidate_rank(tags)
        candidate = (
            db.query(models.StatsRiskCandidate)
            .filter(
                models.StatsRiskCandidate.datasource_id == ds.id,
                models.StatsRiskCandidate.database_name == database_name,
                models.StatsRiskCandidate.table_name == table_name,
            )
            .first()
        )
        if candidate is None:
            candidate = models.StatsRiskCandidate(
                datasource_id=ds.id,
                tenant_name=row.get("tenant_name"),
                database_name=database_name,
                table_name=table_name,
                source=row.get("source"),
                latest_summary=row.get("latest_summary"),
                severity=severity,
                score=score,
                lifecycle_status="active",
                first_seen_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(days=_RISK_CANDIDATE_EXPIRE_DAYS),
            )
            db.add(candidate)
            db.flush()
        else:
            candidate.tenant_name = row.get("tenant_name") or candidate.tenant_name
            candidate.source = row.get("source")
            candidate.latest_summary = row.get("latest_summary")
            candidate.severity = severity
            candidate.score = score
            candidate.lifecycle_status = "active"
            candidate.last_seen_at = now
            candidate.expires_at = now + timedelta(days=_RISK_CANDIDATE_EXPIRE_DAYS)

        seen_candidate_ids.add(candidate.id)
        active_tag_keys: set[str] = set()
        for tag in tags:
            tag_key = _safe_text(tag.get("tag_key"), "")
            if not tag_key:
                continue
            active_tag_keys.add(tag_key)
            record = (
                db.query(models.StatsRiskCandidateTag)
                .filter(
                    models.StatsRiskCandidateTag.candidate_id == candidate.id,
                    models.StatsRiskCandidateTag.tag_key == tag_key,
                )
                .first()
            )
            if record is None:
                record = models.StatsRiskCandidateTag(
                    candidate_id=candidate.id,
                    tag_key=tag_key,
                    tag_label=_safe_text(tag.get("tag_label"), tag_key),
                    severity=_safe_text(tag.get("severity"), "low").lower(),
                    score=float(tag.get("score") or 0.0),
                    facts=_normalize_json_value(tag.get("facts") or {}),
                    active=True,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                db.add(record)
            else:
                record.tag_label = _safe_text(tag.get("tag_label"), tag_key)
                record.severity = _safe_text(tag.get("severity"), "low").lower()
                record.score = float(tag.get("score") or 0.0)
                record.facts = _normalize_json_value(tag.get("facts") or {})
                record.active = True
                record.last_seen_at = now
        stale_tags = (
            db.query(models.StatsRiskCandidateTag)
            .filter(models.StatsRiskCandidateTag.candidate_id == candidate.id)
            .all()
        )
        for stale in stale_tags:
            if stale.tag_key not in active_tag_keys:
                stale.active = False
    return len(seen_candidate_ids)


def _to_risk_candidate_item(candidate: models.StatsRiskCandidate) -> StatsRiskCandidateItem:
    ordered_tags = sorted(
        [tag for tag in candidate.tags if tag.active],
        key=lambda tag: (_SEVERITY_SCORE.get(tag.severity, 0), tag.score),
        reverse=True,
    )
    return StatsRiskCandidateItem(
        candidate_id=candidate.id,
        datasource_id=candidate.datasource_id,
        cluster_key=candidate.datasource.cluster_key if candidate.datasource else None,
        tenant_name=candidate.tenant_name,
        database_name=candidate.database_name,
        table_name=candidate.table_name,
        severity=candidate.severity,  # type: ignore[arg-type]
        score=round(float(candidate.score or 0.0), 2),
        lifecycle_status=candidate.lifecycle_status,  # type: ignore[arg-type]
        source=candidate.source,
        latest_summary=candidate.latest_summary,
        last_seen_at=_dt_to_iso(candidate.last_seen_at),
        tags=[
            StatsRiskCandidateTagItem(
                tag_key=tag.tag_key,
                tag_label=tag.tag_label,
                severity=tag.severity,  # type: ignore[arg-type]
                score=round(float(tag.score or 0.0), 2),
                facts=dict(tag.facts or {}),
            )
            for tag in ordered_tags
        ],
    )


async def _collect_risk_candidates_once(
    db: Session,
    *,
    datasource_id: int,
    lookback_days: int,
    stale_days: int,
    trigger_type: str,
) -> StatsRiskCollectResponse:
    ds = _get_datasource(db, datasource_id)
    run = models.StatsRiskCollectionRun(
        run_id=f"risk-collect-{uuid.uuid4().hex}",
        datasource_id=datasource_id,
        trigger_type=trigger_type,
        status="running",
        started_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()

    try:
        overview, failed, stale, dml = await asyncio.gather(
            get_stats_overview(
                datasource_id=datasource_id,
                lookback_days=lookback_days,
                db=db,
            ),
            get_failed_tables(
                datasource_id=datasource_id,
                lookback_days=lookback_days,
                db=db,
            ),
            get_stale_tables(
                datasource_id=datasource_id,
                stale_days=stale_days,
                db=db,
            ),
            get_dml_changes(
                datasource_id=datasource_id,
                db=db,
            ),
        )
        issues = _build_issue_queue(
            overview=overview,
            failed_items=failed.items,
            stale_items=stale.items,
            dml_items=dml.items,
        )
        rows = _collect_candidate_rows_from_issues(issues)
        now = datetime.utcnow()
        expired_count, _ = _cleanup_risk_candidates(db, datasource_id, now=now)
        collected_count = _upsert_risk_candidates(db, ds, rows)

        active_count = (
            db.query(models.StatsRiskCandidate)
            .filter(
                models.StatsRiskCandidate.datasource_id == datasource_id,
                models.StatsRiskCandidate.lifecycle_status == "active",
            )
            .count()
        )
        run.status = "ready"
        run.finished_at = datetime.utcnow()
        run.summary = f"collected={collected_count}, active={active_count}, expired={expired_count}"
        db.commit()
        return StatsRiskCollectResponse(
            datasource_id=datasource_id,
            collected_tables=collected_count,
            active_candidates=active_count,
            expired_candidates=expired_count,
        )
    except Exception as exc:
        db.rollback()
        run.status = "error"
        run.finished_at = datetime.utcnow()
        run.error_summary = str(exc)
        db.add(run)
        db.commit()
        raise


async def run_stats_risk_collect_for_datasource(
    datasource_id: int,
    *,
    lookback_days: int = 7,
    stale_days: int = 7,
    trigger_type: str = "manual",
) -> StatsRiskCollectResponse:
    db = SessionLocal()
    try:
        return await _collect_risk_candidates_once(
            db,
            datasource_id=datasource_id,
            lookback_days=lookback_days,
            stale_days=stale_days,
            trigger_type=trigger_type,
        )
    finally:
        db.close()


def _list_schedulable_datasource_ids(db: Session) -> list[int]:
    rows = (
        db.query(models.DataSource)
        .filter(models.DataSource.status == "active")
        .order_by(models.DataSource.id.asc())
        .all()
    )
    return [item.id for item in rows if item.id and not _is_sys_datasource(item)]


def _ensure_stats_analysis_schedule(db: Session) -> models.Schedule:
    schedule = (
        db.query(models.Schedule)
        .filter(
            models.Schedule.target_type == "stats_analysis",
            models.Schedule.name == _STATS_ANALYSIS_SCHEDULE_NAME,
        )
        .order_by(models.Schedule.id.asc())
        .first()
    )
    if schedule is not None:
        return schedule

    datasource_ids = _list_schedulable_datasource_ids(db)
    if not datasource_ids:
        raise HTTPException(
            status_code=400, detail="No schedulable datasource found for stats_analysis"
        )
    bootstrap_datasource_id = datasource_ids[0]
    schedule = models.Schedule(
        name=_STATS_ANALYSIS_SCHEDULE_NAME,
        description="系统自动创建的统计信息风险巡检调度，负责定期采集活跃数据源的风险候选。",
        kind="built_in",
        status="active",
        target_type="stats_analysis",
        target_id=bootstrap_datasource_id,
        schedule_type="cron",
        cron_expression=_STATS_ANALYSIS_SCHEDULE_CRON,
        interval_seconds=None,
        timezone="Asia/Shanghai",
        datasource_id=bootstrap_datasource_id,
        function_id=None,
        function_release_id=None,
        input_payload={
            "mode": "batch",
            "lookback_days": 7,
            "stale_days": 7,
            "trigger_type": "auto",
        },
        input_prompt=None,
        max_retries=0,
        retry_backoff_seconds=60,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    worker = get_scheduler_worker()
    if worker is not None:
        worker.request_sync_schedule(schedule.id, timeout_seconds=3.0)
    return schedule


def _build_collect_response_from_db(
    db: Session, datasource_id: int, *, fallback_collected: int = 0
) -> StatsRiskCollectResponse:
    active_count = (
        db.query(models.StatsRiskCandidate)
        .filter(
            models.StatsRiskCandidate.datasource_id == datasource_id,
            models.StatsRiskCandidate.lifecycle_status == "active",
        )
        .count()
    )
    expired_count = (
        db.query(models.StatsRiskCandidate)
        .filter(
            models.StatsRiskCandidate.datasource_id == datasource_id,
            models.StatsRiskCandidate.lifecycle_status == "expired",
        )
        .count()
    )
    collected = max(int(fallback_collected), 0)
    return StatsRiskCollectResponse(
        datasource_id=datasource_id,
        collected_tables=collected,
        active_candidates=active_count,
        expired_candidates=expired_count,
    )


def ensure_stats_analysis_schedule_singleton() -> dict[str, int]:
    db = SessionLocal()
    try:
        existed = (
            db.query(models.Schedule.id)
            .filter(
                models.Schedule.target_type == "stats_analysis",
                models.Schedule.name == _STATS_ANALYSIS_SCHEDULE_NAME,
            )
            .first()
            is not None
        )
        schedule = _ensure_stats_analysis_schedule(db)
        legacy_schedules = (
            db.query(models.Schedule)
            .filter(
                models.Schedule.target_type == "stats_analysis",
                models.Schedule.id != schedule.id,
            )
            .all()
        )
        removed = 0
        for legacy in legacy_schedules:
            db.delete(legacy)
            removed += 1
        db.commit()
        return {"created": 0 if existed else 1, "removed_legacy": removed}
    finally:
        db.close()


def _build_issue_from_candidate(candidate: models.StatsRiskCandidate) -> StatsIssueItem:
    active_tags = [tag for tag in candidate.tags if tag.active]
    primary_tag = max(
        active_tags,
        key=lambda tag: (_SEVERITY_SCORE.get(tag.severity, 0), tag.score),
        default=None,
    )
    facts: dict[str, Any] = {
        "candidate_id": candidate.id,
        "tag_count": len(active_tags),
        "tags": [
            {
                "tag_key": tag.tag_key,
                "tag_label": tag.tag_label,
                "severity": tag.severity,
                "score": float(tag.score or 0.0),
                "facts": _normalize_json_value(tag.facts or {}),
            }
            for tag in active_tags
        ],
    }
    if primary_tag and primary_tag.facts:
        facts.update(_normalize_json_value(primary_tag.facts))

    kind: Literal["failed_table", "stale_stats", "dml_change"] = "stale_stats"
    if any(tag.tag_key == "collect_failed" for tag in active_tags):
        kind = "failed_table"
    elif any(tag.tag_key == "high_dml_change" for tag in active_tags):
        kind = "dml_change"

    title = f"{candidate.database_name}.{candidate.table_name} 风险分析"
    summary = candidate.latest_summary or "候选池深度分析任务"
    return StatsIssueItem(
        issue_id=f"risk:{candidate.id}",
        kind=kind,
        severity=candidate.severity,  # type: ignore[arg-type]
        title=title,
        summary=summary,
        datasource_id=candidate.datasource_id,
        cluster_key=candidate.datasource.cluster_key if candidate.datasource else None,
        tenant_name=candidate.tenant_name,
        database_name=candidate.database_name,
        table_name=candidate.table_name,
        facts=facts,
    )


def _build_issue_from_risk_candidate_payload(candidate: StatsRiskCandidateItem) -> StatsIssueItem:
    facts: dict[str, Any] = {
        "candidate_id": candidate.candidate_id,
        "tag_count": len(candidate.tags or []),
        "tags": [
            {
                "tag_key": tag.tag_key,
                "tag_label": tag.tag_label,
                "severity": tag.severity,
                "score": float(tag.score or 0.0),
                "facts": _normalize_json_value(tag.facts or {}),
            }
            for tag in candidate.tags
        ],
    }
    primary_tag = max(
        candidate.tags,
        key=lambda tag: (_SEVERITY_SCORE.get(tag.severity, 0), tag.score),
        default=None,
    )
    if primary_tag and primary_tag.facts:
        facts.update(_normalize_json_value(primary_tag.facts or {}))

    kind: Literal["failed_table", "stale_stats", "dml_change"] = "stale_stats"
    if any(tag.tag_key == "collect_failed" for tag in candidate.tags):
        kind = "failed_table"
    elif any(tag.tag_key == "high_dml_change" for tag in candidate.tags):
        kind = "dml_change"

    return StatsIssueItem(
        issue_id=f"risk:{candidate.candidate_id}",
        kind=kind,
        severity=candidate.severity,
        title=f"{candidate.database_name}.{candidate.table_name} 风险分析",
        summary=candidate.latest_summary or "候选池深度分析任务",
        datasource_id=candidate.datasource_id,
        cluster_key=candidate.cluster_key,
        tenant_name=candidate.tenant_name,
        database_name=candidate.database_name,
        table_name=candidate.table_name,
        facts=facts,
    )


def _run_candidate_analysis_task(run_id: str) -> None:
    db = SessionLocal()
    try:
        run = (
            db.query(models.StatsRiskAnalysisRun)
            .filter(models.StatsRiskAnalysisRun.run_id == run_id)
            .first()
        )
        if run is None:
            return
        candidate = (
            db.query(models.StatsRiskCandidate)
            .filter(models.StatsRiskCandidate.id == run.candidate_id)
            .first()
        )
        if candidate is None:
            run.status = "error"
            run.error_summary = "risk candidate not found"
            run.finished_at = datetime.utcnow()
            db.commit()
            return
        run.status = "running"
        run.started_at = datetime.utcnow()
        db.commit()

        issue = _build_issue_from_candidate(candidate)
        llm_result = asyncio.run(
            _call_llm_for_diagnosis(
                issue=issue,
                mode="deep",
            )
        )
        if llm_result is None:
            status, result = _build_heuristic_result(issue, "deep")
        else:
            status = "needs_clarification" if llm_result.missing_facts else "ready"
            result = llm_result

        run.status = status
        run.summary = result.headline
        run.result_payload = result.model_dump()
        run.error_summary = None
        run.finished_at = datetime.utcnow()
        db.commit()
        logger.info("stats_risk_analysis_done run_id=%s status=%s", run_id, status)
    except Exception as exc:
        db.rollback()
        logger.exception("stats_risk_analysis_failed run_id=%s error=%s", run_id, exc)
        run = (
            db.query(models.StatsRiskAnalysisRun)
            .filter(models.StatsRiskAnalysisRun.run_id == run_id)
            .first()
        )
        if run:
            run.status = "error"
            run.error_summary = str(exc)
            run.finished_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


async def _run_candidate_analysis_in_session(db: Session, run: models.StatsRiskAnalysisRun) -> None:
    candidate = (
        db.query(models.StatsRiskCandidate)
        .filter(models.StatsRiskCandidate.id == run.candidate_id)
        .first()
    )
    if candidate is None:
        run.status = "error"
        run.error_summary = "risk candidate not found"
        run.finished_at = datetime.utcnow()
        return

    run.status = "running"
    run.started_at = datetime.utcnow()

    issue = _build_issue_from_candidate(candidate)
    llm_result = await _call_llm_for_diagnosis(
        issue=issue,
        mode="deep",
    )
    if llm_result is None:
        status, result = _build_heuristic_result(issue, "deep")
    else:
        status = "needs_clarification" if llm_result.missing_facts else "ready"
        result = llm_result

    run.status = status
    run.summary = result.headline
    run.result_payload = result.model_dump()
    run.error_summary = None
    run.finished_at = datetime.utcnow()


async def _execute_query_candidates(
    ds: models.DataSource,
    candidates: list[tuple[str, str]],
) -> tuple[str | None, list[dict[str, Any]]]:
    pool = get_db_pool()
    for source, sql in candidates:
        try:
            result = await pool.execute_query(ds, sql)
            rows = _rows_to_plain_dicts(result)
            if rows:
                return source, rows
        except Exception as exc:
            logger.info("stats_analysis_query_candidate_failed source=%s error=%s", source, exc)
    return None, []


def _table_gather_history_candidates(
    ds: models.DataSource, *, safe_table: str
) -> list[tuple[str, str]]:
    sys_candidate = (
        "table_gather_history_sys",
        f"""
SELECT NULL AS tenant_name, NULL AS owner, NULL AS table_name,
       h.task_id, NULL AS status, h.ret_code, h.start_time, h.end_time, h.memory_used,
       TIMESTAMPDIFF(SECOND, h.start_time, h.end_time) AS gather_seconds,
       h.stat_refresh_failed_list, h.properties
FROM oceanbase.__ALL_VIRTUAL_TABLE_OPT_STAT_GATHER_HISTORY h
JOIN oceanbase.__ALL_VIRTUAL_TABLE t ON h.table_id = t.table_id AND h.tenant_id = t.tenant_id
WHERE t.table_name = '{safe_table}'
ORDER BY h.start_time DESC
LIMIT 10
""",
    )
    tenant_candidate = (
        "table_gather_history_tenant",
        f"""
SELECT NULL AS tenant_name, owner, table_name, task_id, status,
       start_time, end_time, memory_used,
       TIMESTAMPDIFF(SECOND, start_time, end_time) AS gather_seconds,
       stat_refresh_failed_list, properties
FROM oceanbase.DBA_OB_TABLE_OPT_STAT_GATHER_HISTORY
WHERE table_name = '{safe_table}'
ORDER BY start_time DESC
LIMIT 10
""",
    )
    return (
        [sys_candidate, tenant_candidate]
        if _is_sys_datasource(ds)
        else [tenant_candidate, sys_candidate]
    )


def _find_sys_datasource_for_cluster(
    db: Session, ds: models.DataSource
) -> models.DataSource | None:
    """Find the sys datasource in the same cluster, for cross-tenant ret_code queries."""
    if _is_sys_datasource(ds):
        return ds
    if not ds.cluster_key:
        return None
    return (
        db.query(models.DataSource)
        .filter(
            models.DataSource.cluster_key == ds.cluster_key,
            models.DataSource.tenant_role == "sys",
        )
        .first()
    )


async def _check_tenant_configs(
    db: Session,
    cluster_key: str,
    lookback_days: int = 7,
    datasource_id: int | None = None,
) -> list[StatsTenantConfigCheck]:
    """Scan tenant datasources in the cluster for auto-gather config issues.

    When *datasource_id* is given, only that single datasource is checked
    (still subject to the non-sys filter).
    """
    q = db.query(models.DataSource).filter(
        models.DataSource.cluster_key == cluster_key,
        models.DataSource.tenant_role != "sys",
    )
    if datasource_id is not None:
        q = q.filter(models.DataSource.id == datasource_id)
    tenant_datasources = q.all()
    if not tenant_datasources:
        return []

    pool = get_db_pool()
    checks: list[StatsTenantConfigCheck] = []

    for tds in tenant_datasources:
        tenant_label = tds.name or f"ds-{tds.id}"
        auto_gather_enabled: bool | None = None
        enabled_windows = 0
        recent_task_count = 0
        query_failures = 0

        # 1. Check _enable_auto_stat_gather parameter
        try:
            result = await pool.execute_query(
                tds, "SHOW PARAMETERS LIKE '_enable_auto_stat_gather'"
            )
            rows = _rows_to_plain_dicts(result)
            if rows:
                val = str(rows[0].get("value") or rows[0].get("VALUE") or "").strip().lower()
                auto_gather_enabled = val in ("true", "1", "on", "yes")
            else:
                auto_gather_enabled = None
        except Exception as exc:
            logger.warning("tenant_config_check auto_gather failed ds=%s error=%s", tds.id, exc)
            query_failures += 1

        # 2. Check scheduler windows
        try:
            result = await pool.execute_query(
                tds,
                """
SELECT COUNT(*) AS total, SUM(CASE WHEN enabled IN ('TRUE','1','true') THEN 1 ELSE 0 END) AS enabled_cnt
FROM oceanbase.DBA_SCHEDULER_JOBS
WHERE job_name IN ('MONDAY_WINDOW','TUESDAY_WINDOW','WEDNESDAY_WINDOW',
  'THURSDAY_WINDOW','FRIDAY_WINDOW','SATURDAY_WINDOW','SUNDAY_WINDOW')
""",
            )
            rows = _rows_to_plain_dicts(result)
            if rows:
                enabled_windows = int(rows[0].get("enabled_cnt") or 0)
        except Exception as exc:
            logger.warning("tenant_config_check windows failed ds=%s error=%s", tds.id, exc)
            query_failures += 1

        # 3. Check recent AUTO GATHER task count
        try:
            result = await pool.execute_query(
                tds,
                """
SELECT COUNT(*) AS cnt
FROM oceanbase.DBA_OB_TASK_OPT_STAT_GATHER_HISTORY
WHERE type = 'AUTO GATHER'
  AND start_time > DATE_SUB(NOW(), INTERVAL %s DAY)
""",
                params=[lookback_days],
            )
            rows = _rows_to_plain_dicts(result)
            if rows:
                recent_task_count = int(rows[0].get("cnt") or 0)
        except Exception as exc:
            logger.warning("tenant_config_check tasks failed ds=%s error=%s", tds.id, exc)
            query_failures += 1

        # If all 3 queries failed, the tenant is unreachable
        if query_failures >= 3:
            checks.append(
                StatsTenantConfigCheck(
                    tenant_name=tenant_label,
                    datasource_id=tds.id,
                    issue_type="unreachable",
                    issue_label="租户不可达",
                    suggestion_sql="-- 请检查该租户的数据源连接配置和网络可达性",
                )
            )
            continue

        # Determine issue (priority: disabled > no windows > no tasks)
        if auto_gather_enabled is False:
            checks.append(
                StatsTenantConfigCheck(
                    tenant_name=tenant_label,
                    datasource_id=tds.id,
                    auto_gather_enabled=False,
                    enabled_windows=enabled_windows,
                    recent_task_count=recent_task_count,
                    issue_type="auto_gather_disabled",
                    issue_label="自动采集未启用",
                    suggestion_sql="ALTER SYSTEM SET _enable_auto_stat_gather = true;",
                )
            )
        elif enabled_windows == 0:
            checks.append(
                StatsTenantConfigCheck(
                    tenant_name=tenant_label,
                    datasource_id=tds.id,
                    auto_gather_enabled=auto_gather_enabled,
                    enabled_windows=0,
                    recent_task_count=recent_task_count,
                    issue_type="no_windows",
                    issue_label="调度窗口全部关闭",
                    suggestion_sql=(
                        "-- 启用所有调度窗口（逐条执行）\n"
                        "CALL DBMS_SCHEDULER.ENABLE('MONDAY_WINDOW');\n"
                        "CALL DBMS_SCHEDULER.ENABLE('TUESDAY_WINDOW');\n"
                        "CALL DBMS_SCHEDULER.ENABLE('WEDNESDAY_WINDOW');\n"
                        "CALL DBMS_SCHEDULER.ENABLE('THURSDAY_WINDOW');\n"
                        "CALL DBMS_SCHEDULER.ENABLE('FRIDAY_WINDOW');\n"
                        "CALL DBMS_SCHEDULER.ENABLE('SATURDAY_WINDOW');\n"
                        "CALL DBMS_SCHEDULER.ENABLE('SUNDAY_WINDOW');"
                    ),
                )
            )
        elif enabled_windows < 7:
            checks.append(
                StatsTenantConfigCheck(
                    tenant_name=tenant_label,
                    datasource_id=tds.id,
                    auto_gather_enabled=auto_gather_enabled,
                    enabled_windows=enabled_windows,
                    recent_task_count=recent_task_count,
                    issue_type="partial_windows",
                    issue_label=f"调度窗口部分启用（{enabled_windows}/7）",
                    suggestion_sql=(
                        "-- 补齐未启用的调度窗口（逐条执行）\n"
                        "CALL DBMS_SCHEDULER.ENABLE('MONDAY_WINDOW');\n"
                        "CALL DBMS_SCHEDULER.ENABLE('TUESDAY_WINDOW');\n"
                        "CALL DBMS_SCHEDULER.ENABLE('WEDNESDAY_WINDOW');\n"
                        "CALL DBMS_SCHEDULER.ENABLE('THURSDAY_WINDOW');\n"
                        "CALL DBMS_SCHEDULER.ENABLE('FRIDAY_WINDOW');\n"
                        "CALL DBMS_SCHEDULER.ENABLE('SATURDAY_WINDOW');\n"
                        "CALL DBMS_SCHEDULER.ENABLE('SUNDAY_WINDOW');"
                    ),
                )
            )
        elif recent_task_count == 0 and auto_gather_enabled is not False:
            checks.append(
                StatsTenantConfigCheck(
                    tenant_name=tenant_label,
                    datasource_id=tds.id,
                    auto_gather_enabled=auto_gather_enabled,
                    enabled_windows=enabled_windows,
                    recent_task_count=0,
                    issue_type="no_recent_tasks",
                    issue_label=f"近 {lookback_days} 天无自动采集任务",
                    suggestion_sql=(
                        "-- 手动触发一次全表统计信息收集\n"
                        "CALL DBMS_STATS.GATHER_SCHEMA_STATS(NULL, degree=>4);"
                    ),
                )
            )
        else:
            checks.append(
                StatsTenantConfigCheck(
                    tenant_name=tenant_label,
                    datasource_id=tds.id,
                    auto_gather_enabled=auto_gather_enabled,
                    enabled_windows=enabled_windows,
                    recent_task_count=recent_task_count,
                    issue_type="healthy",
                    issue_label="配置正常",
                    suggestion_sql="",
                )
            )

    return checks


async def _fetch_table_gather_history_rows(
    ds: models.DataSource,
    *,
    table_name: str | None,
    db: Session | None = None,
) -> tuple[str | None, list[dict[str, Any]], list[str]]:
    if not table_name:
        return None, [], ["table_gather_history"]
    safe_table = str(table_name).replace("'", "''")

    # Always try sys-first for ret_code visibility, even for tenant datasources
    sys_ds = _find_sys_datasource_for_cluster(db, ds) if db and not _is_sys_datasource(ds) else None
    if sys_ds:
        candidates = _table_gather_history_candidates(sys_ds, safe_table=safe_table)
        source, rows = await _execute_query_candidates(sys_ds, candidates)
        if rows:
            return source, rows, []

    # Fallback to current datasource candidates
    source, rows = await _execute_query_candidates(
        ds, _table_gather_history_candidates(ds, safe_table=safe_table)
    )
    if not rows:
        return source, [], ["table_gather_history"]
    return source, rows, []


async def _fetch_task_history_context(
    ds: models.DataSource,
    *,
    task_ids: list[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    normalized_ids = [str(task_id).strip() for task_id in task_ids if str(task_id or "").strip()]
    if not normalized_ids:
        return {}, ["task_history"]
    safe_ids = ",".join("'{}'".format(value.replace("'", "''")) for value in normalized_ids)
    source, rows = await _execute_query_candidates(
        ds,
        [
            (
                "task_history",
                f"""
SELECT task_id, type, status, start_time, end_time, table_count, failed_count
FROM oceanbase.DBA_OB_TASK_OPT_STAT_GATHER_HISTORY
WHERE task_id IN ({safe_ids})
ORDER BY start_time DESC
LIMIT 20
""",
            )
        ],
    )
    if not rows:
        return {}, ["task_history"]
    mapped = {str(row.get("task_id")): row for row in rows if row.get("task_id") is not None}
    return mapped, [] if source else ["task_history"]


def _infer_trigger_type(raw_type: Any) -> str:
    text = str(raw_type or "").strip()
    upper = text.upper()
    if "AUTO" in upper:
        return "自动收集"
    if "MANUAL" in upper:
        return "手动收集"
    if text:
        return text
    return "-"


def _parse_properties(raw: Any) -> dict[str, str]:
    """Parse PROPERTIES like 'GRANULARITY:AUTO;METHOD_OPT:FOR ALL COLUMNS SIZE AUTO;DEGREE:1'."""
    text = str(raw or "").strip()
    if not text:
        return {}
    result = {}
    for part in text.split(";"):
        if ":" in part:
            key, _, value = part.partition(":")
            result[key.strip().upper()] = value.strip()
    return result


def _extract_error_code(reason: str | None, fallback: Any = None) -> str:
    for candidate in (str(fallback or "").strip(), str(reason or "").strip()):
        if not candidate:
            continue
        match = re.search(r"(-\d+)", candidate)
        if match:
            return match.group(1)
    return "-"


def _build_drawer_fields(items: list[tuple[str, Any, str | None]]) -> list[StatsDrawerDetailField]:
    fields: list[StatsDrawerDetailField] = []
    for label, value, source in items:
        text = _safe_text(value)
        if text == "-":
            continue
        fields.append(StatsDrawerDetailField(label=label, value=text, source=source))
    return fields


async def _fetch_scheduler_run_detail(
    ds: models.DataSource,
) -> tuple[list[StatsDiagnosisEvidence], list[str]]:
    source, rows = await _execute_query_candidates(
        ds,
        [
            (
                "scheduler_run_detail",
                """
SELECT job_name, status, additional_info, errors, req_start_date, actual_start_date
FROM oceanbase.DBA_SCHEDULER_JOB_RUN_DETAILS
WHERE job_name IN (
  'MONDAY_WINDOW','TUESDAY_WINDOW','WEDNESDAY_WINDOW',
  'THURSDAY_WINDOW','FRIDAY_WINDOW','SATURDAY_WINDOW','SUNDAY_WINDOW'
)
ORDER BY actual_start_date DESC
LIMIT 3
""",
            ),
            (
                "tenant_scheduler_run_detail",
                """
SELECT job_name, status, additional_info, errors, req_start_date, actual_start_date
FROM oceanbase.__ALL_TENANT_SCHEDULER_JOB_RUN_DETAIL
ORDER BY actual_start_date DESC
LIMIT 3
""",
            ),
        ],
    )
    if not rows:
        return [], ["scheduler_run_detail"]

    latest = rows[0]
    evidence = [
        StatsDiagnosisEvidence(
            label="最近调度状态",
            value=_safe_text(latest.get("status"), "UNKNOWN"),
            source=source,
        ),
        StatsDiagnosisEvidence(
            label="最近调度时间",
            value=_safe_text(latest.get("actual_start_date")),
            source=source,
        ),
    ]
    errors = _safe_text(latest.get("errors"), "")
    if errors:
        evidence.append(StatsDiagnosisEvidence(label="最近错误码", value=errors, source=source))
    return evidence, []


async def _fetch_table_gather_history(
    ds: models.DataSource,
    *,
    table_name: str | None,
) -> tuple[list[StatsDiagnosisEvidence], dict[str, Any], list[str]]:
    source, rows, missing = await _fetch_table_gather_history_rows(ds, table_name=table_name)
    if not rows:
        return [], {}, missing

    latest = rows[0]
    durations = [
        int(row.get("gather_seconds") or 0) for row in rows if row.get("gather_seconds") is not None
    ]
    status = _safe_text(latest.get("status") or latest.get("ret_code"), "UNKNOWN")
    facts = {
        "history_latest_status": status,
        "history_latest_start_time": _safe_text(latest.get("start_time")),
        "history_latest_end_time": _safe_text(latest.get("end_time")),
        "history_max_gather_seconds": max(durations) if durations else None,
        "history_sample_count": len(rows),
    }
    evidence = [
        StatsDiagnosisEvidence(label="最近一次收集状态", value=status, source=source),
        StatsDiagnosisEvidence(
            label="历史最长耗时(秒)",
            value=_safe_text(facts.get("history_max_gather_seconds")),
            source=source,
        ),
    ]
    if latest.get("memory_used") is not None:
        evidence.append(
            StatsDiagnosisEvidence(
                label="最近一次内存使用",
                value=_safe_text(latest.get("memory_used")),
                source=source,
            )
        )
    return evidence, facts, []


async def _fetch_column_stats_summary(
    ds: models.DataSource,
    *,
    table_name: str | None,
) -> tuple[list[StatsDiagnosisEvidence], dict[str, Any], list[str]]:
    if not table_name:
        return [], {}, ["column_stats"]

    safe_table = str(table_name).replace("'", "''")
    source, rows = await _execute_query_candidates(
        ds,
        [
            (
                "column_stats",
                f"""
SELECT column_name, num_distinct, num_buckets, histogram, last_analyzed
FROM oceanbase.DBA_TAB_COL_STATISTICS
WHERE table_name = '{safe_table}'
ORDER BY num_distinct DESC
LIMIT 5
""",
            ),
        ],
    )
    if not rows:
        return [], {}, ["column_stats"]

    histogram_missing = 0
    max_ndv = 0
    for row in rows:
        ndv = _int(row, "num_distinct") or 0
        buckets = _int(row, "num_buckets") or 0
        histogram = _safe_text(row.get("histogram"), "")
        max_ndv = max(max_ndv, ndv)
        if ndv >= 100 and (buckets <= 1 or histogram in {"", "NONE"}):
            histogram_missing += 1

    facts = {
        "column_stats_top_ndv": max_ndv,
        "column_stats_histogram_missing": histogram_missing,
    }
    evidence = [
        StatsDiagnosisEvidence(label="高 NDV 列数", value=_safe_text(len(rows)), source=source),
        StatsDiagnosisEvidence(
            label="缺直方图高 NDV 列", value=_safe_text(histogram_missing), source=source
        ),
    ]
    return evidence, facts, []


async def _collect_deep_issue_context(
    datasource_id: int,
    issue: StatsIssueItem,
) -> tuple[dict[str, Any], list[StatsDiagnosisEvidence], list[str]]:
    db = SessionLocal()
    try:
        ds = _get_datasource(db, datasource_id)
    finally:
        db.close()

    extra_facts: dict[str, Any] = {}
    extra_evidence: list[StatsDiagnosisEvidence] = []
    missing_facts: list[str] = []

    if issue.kind == "scheduling":
        evidence, missing = await _fetch_scheduler_run_detail(ds)
        extra_evidence.extend(evidence)
        missing_facts.extend(missing)
        return extra_facts, extra_evidence, missing_facts

    history_evidence, history_facts, history_missing = await _fetch_table_gather_history(
        ds,
        table_name=issue.table_name,
    )
    extra_evidence.extend(history_evidence)
    extra_facts.update(history_facts)
    missing_facts.extend(history_missing)

    if issue.kind in {"stale_stats", "dml_change", "failed_table"}:
        column_evidence, column_facts, column_missing = await _fetch_column_stats_summary(
            ds,
            table_name=issue.table_name,
        )
        extra_evidence.extend(column_evidence)
        extra_facts.update(column_facts)
        missing_facts.extend(column_missing)

    return extra_facts, extra_evidence, missing_facts


def _heuristic_actions(verdict: str) -> list[StatsDiagnosisAction]:
    if verdict == "large_table_timeout":
        return [
            StatsDiagnosisAction(
                title="优先检查 method_opt 是否可降低直方图开销",
                rationale="超时场景里直方图常是主要耗时来源。",
                risk="仅适用于列分布相对均匀的场景。",
                execution_window="业务低峰期",
            ),
            StatsDiagnosisAction(
                title="评估并行度 degree 是否可提升到 4-8",
                rationale="并行度过低时容易拖长收集时长。",
                risk="并行度过高会与业务争抢资源。",
                execution_window="业务低峰期",
            ),
        ]
    if verdict == "window_insufficient":
        return [
            StatsDiagnosisAction(
                title="调整 MONDAY~SUNDAY_WINDOW 的执行时间",
                rationale="窗口时长不足会导致批量表长期未完成。",
                risk="调整时间需避开业务高峰。",
                execution_window="非高峰时段",
            ),
        ]
    if verdict == "scheduling_window_abnormal":
        return [
            StatsDiagnosisAction(
                title="先核对窗口 enabled 状态与最近 run detail",
                rationale="调度链路异常时先恢复任务可用性。",
                risk="直接启用前要确认窗口时间正确。",
                execution_window="立即处理",
            ),
        ]
    if verdict == "stale_risk_from_dml":
        return [
            StatsDiagnosisAction(
                title="优先对高变化表补采统计信息",
                rationale="DML 变化显著时旧统计信息与真实分布偏差会扩大。",
                risk="业务高峰期补采可能导致 plan cache 波动。",
                execution_window="业务低峰期",
            ),
        ]
    return [
        StatsDiagnosisAction(
            title="先在低峰期重试并复核失败原因",
            rationale="通用失败场景优先恢复收集成功率。",
            risk="若失败持续需进一步拆分场景。",
            execution_window="业务低峰期",
        ),
    ]


def _build_heuristic_result(
    issue: StatsIssueItem,
    mode: Literal["summary", "deep"],
    *,
    extra_facts: dict[str, Any] | None = None,
    extra_evidence: list[StatsDiagnosisEvidence] | None = None,
    upstream_missing_facts: list[str] | None = None,
) -> tuple[str, StatsDiagnosisResult]:
    facts = _extract_issue_facts(issue)
    facts.update({str(key): _safe_text(value) for key, value in (extra_facts or {}).items()})
    missing_facts: list[str] = list(upstream_missing_facts or [])
    if issue.kind == "failed_table" and not facts.get("error_reason"):
        missing_facts.append("error_reason")
    if issue.kind == "dml_change" and not facts.get("row_change_delta"):
        missing_facts.append("row_change_delta")

    verdict, reasoning = _detect_verdict(issue)
    if (
        issue.kind == "failed_table"
        and int(facts.get("history_max_gather_seconds", "0") or "0") >= 1800
    ):
        verdict = "large_table_timeout"
        reasoning = "历史收集持续超过 30 分钟，更像大表收集超时。"
    if (
        issue.kind in {"stale_stats", "dml_change"}
        and int(facts.get("column_stats_histogram_missing", "0") or "0") > 0
    ):
        verdict = "missing_histogram"
        reasoning = "关键高 NDV 列存在缺直方图特征，统计信息质量存在明显缺口。"

    status: Literal["ready", "degraded", "needs_clarification"] = "ready"
    if missing_facts:
        status = "needs_clarification" if mode == "deep" else "degraded"
        reasoning = f"{reasoning} 但缺少关键事实：{', '.join(missing_facts)}。"

    evidence = [
        StatsDiagnosisEvidence(label="问题类型", value=issue.kind, source="facts"),
        StatsDiagnosisEvidence(label="严重程度", value=issue.severity, source="facts"),
    ]
    if issue.table_name:
        evidence.append(StatsDiagnosisEvidence(label="表", value=issue.table_name, source="facts"))
    for key in ("error_reason", "gather_seconds", "row_change_delta", "stats_state"):
        val = facts.get(key)
        if val and val != "-":
            evidence.append(StatsDiagnosisEvidence(label=key, value=val, source="facts"))
    evidence = _merge_evidence(evidence, extra_evidence or [])

    diagnosis_path: list[str] = []
    risks: list[str] = []
    if mode == "deep":
        diagnosis_path = [
            "收集基础事实",
            "补充调度/历史/列统计证据",
            "判断异常类型",
            "匹配运维建议",
            "输出风险提醒和缺失项",
        ]
        if verdict in {"large_table_timeout", "window_insufficient", "missing_histogram"}:
            risks.append("统计信息收集默认可能触发 plan cache 刷新，需避开高峰期。")
        if issue.kind in {"stale_stats", "dml_change"}:
            risks.append("建议先确认关键查询是否受影响，再决定是否立即收集。")
        if verdict == "missing_histogram":
            risks.append("直方图策略调整后需观察执行计划是否出现新的偏差。")

    return (
        status,
        StatsDiagnosisResult(
            headline=issue.title,
            verdict=verdict,
            reasoning=reasoning,
            evidence=evidence,
            next_actions=_heuristic_actions(verdict),
            missing_facts=_merge_missing_facts(missing_facts),
            diagnosis_path=diagnosis_path,
            risks=risks,
        ),
    )


async def _call_llm_for_diagnosis(
    issue: StatsIssueItem,
    mode: Literal["summary", "deep"],
    *,
    extra_facts: dict[str, Any] | None = None,
    extra_evidence: list[StatsDiagnosisEvidence] | None = None,
    missing_facts: list[str] | None = None,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
) -> StatsDiagnosisResult | None:
    issue_payload = _normalize_json_value(
        issue.model_dump() if hasattr(issue, "model_dump") else issue.dict()
    )
    issue_payload["facts"] = {
        **(issue_payload.get("facts") or {}),
        **_normalize_json_value(extra_facts or {}),
    }
    if extra_evidence:
        issue_payload["supporting_evidence"] = [item.model_dump() for item in extra_evidence]
    if missing_facts:
        issue_payload["known_missing_facts"] = missing_facts
    system_prompt = (
        "你是 stats_analysis_system_agent，专门负责 OceanBase 统计信息优化诊断。"
        "你必须只基于输入 facts/evidence 推理，不能编造。"
        "输出必须可执行、可验证，禁止空话。"
        "对于 deep 模式，必须给出覆盖收集失败、窗口策略、NDV/直方图策略、执行风险的综合建议。"
        "证据不足时必须在 missing_facts 列出缺失项。"
    )
    user_prompt = (
        "请基于如下 issue 生成诊断结果。\n"
        f"mode={mode}\n"
        "输出 JSON 字段：headline, verdict, reasoning, evidence[], next_actions[], missing_facts[], diagnosis_path[], risks[]。\n"
        "evidence 元素字段：label, value, source。\n"
        "next_actions 元素字段：title, rationale, risk, execution_window。\n"
        "issue:\n"
        f"{json.dumps(issue_payload, ensure_ascii=False)}"
    )
    content = ""
    try:
        llm = get_llm_client()
        async for chunk in llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=True,
            temperature=0.1,
        ):
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if isinstance(delta, dict) and delta.get("content"):
                delta_text = str(delta.get("content"))
                content += delta_text
                if on_delta and delta_text:
                    await on_delta(delta_text)
            message = choice.get("message")
            if isinstance(message, dict) and message.get("content"):
                message_text = str(message.get("content"))
                content += message_text
                if on_delta and message_text:
                    await on_delta(message_text)
    except Exception as exc:
        logger.warning("stats_diagnosis_llm_failed issue=%s error=%s", issue.issue_id, exc)
        return None

    payload = _parse_llm_json_object(content)
    if not payload:
        return None
    try:
        evidence = [
            StatsDiagnosisEvidence(
                label=_safe_text(item.get("label"), "evidence"),
                value=_safe_text(item.get("value")),
                source=_safe_text(item.get("source"), "llm"),
            )
            for item in (payload.get("evidence") or [])
            if isinstance(item, dict)
        ]
        actions = [
            StatsDiagnosisAction(
                title=_safe_text(item.get("title"), "待确认动作"),
                rationale=_safe_text(item.get("rationale"), None),
                risk=_safe_text(item.get("risk"), None),
                execution_window=_safe_text(item.get("execution_window"), None),
            )
            for item in (payload.get("next_actions") or [])
            if isinstance(item, dict)
        ]
        return StatsDiagnosisResult(
            headline=_safe_text(payload.get("headline"), issue.title),
            verdict=_safe_text(payload.get("verdict"), "unknown"),
            reasoning=_safe_text(
                payload.get("reasoning"), "LLM 未返回完整推理，已回退到最小说明。"
            ),
            evidence=evidence,
            next_actions=actions,
            missing_facts=[
                _safe_text(v) for v in (payload.get("missing_facts") or []) if _safe_text(v)
            ],
            diagnosis_path=[
                _safe_text(v) for v in (payload.get("diagnosis_path") or []) if _safe_text(v)
            ],
            risks=[_safe_text(v) for v in (payload.get("risks") or []) if _safe_text(v)],
        )
    except Exception as exc:
        logger.warning("stats_diagnosis_llm_parse_failed issue=%s error=%s", issue.issue_id, exc)
        return None


async def _update_task(
    task_id: str,
    *,
    status: Literal["pending", "running", "ready", "degraded", "needs_clarification", "error"]
    | None = None,
    result: StatsDiagnosisResult | None = None,
) -> None:
    async with _DIAG_TASKS_LOCK:
        record = _DIAG_TASKS.get(task_id, {"task_id": task_id, "status": "pending", "result": None})
        if status is not None:
            record["status"] = status
        if result is not None:
            record["result"] = result
        _DIAG_TASKS[task_id] = record


async def _run_diagnosis_task(
    task_id: str,
    *,
    datasource_id: int,
    issue: StatsIssueItem,
    mode: Literal["summary", "deep"],
) -> None:
    await _update_task(task_id, status="running")
    logger.info(
        "stats_diagnosis_task_start task_id=%s mode=%s issue=%s", task_id, mode, issue.issue_id
    )
    try:
        extra_facts: dict[str, Any] = {}
        extra_evidence: list[StatsDiagnosisEvidence] = []
        upstream_missing_facts: list[str] = []
        if mode == "deep":
            extra_facts, extra_evidence, upstream_missing_facts = await _collect_deep_issue_context(
                datasource_id,
                issue,
            )
        llm_result = await _call_llm_for_diagnosis(
            issue,
            mode,
            extra_facts=extra_facts,
            extra_evidence=extra_evidence,
            missing_facts=upstream_missing_facts,
        )
        if llm_result is not None:
            llm_result.evidence = _merge_evidence(llm_result.evidence, extra_evidence)
            llm_result.missing_facts = _merge_missing_facts(
                llm_result.missing_facts, upstream_missing_facts
            )
            status: Literal["ready", "degraded", "needs_clarification"] = (
                "needs_clarification"
                if llm_result.missing_facts and mode == "deep"
                else "degraded"
                if llm_result.missing_facts
                else "ready"
            )
            await _update_task(task_id, status=status, result=llm_result)
            logger.info(
                "stats_diagnosis_task_success task_id=%s mode=%s status=%s", task_id, mode, status
            )
            return

        fallback_status, fallback_result = _build_heuristic_result(
            issue,
            mode,
            extra_facts=extra_facts,
            extra_evidence=extra_evidence,
            upstream_missing_facts=upstream_missing_facts,
        )
        await _update_task(task_id, status=fallback_status, result=fallback_result)
        logger.info(
            "stats_diagnosis_task_fallback task_id=%s mode=%s status=%s",
            task_id,
            mode,
            fallback_status,
        )
    except Exception as exc:
        logger.exception(
            "stats_diagnosis_task_failed task_id=%s issue=%s error=%s", task_id, issue.issue_id, exc
        )
        _, fallback_result = _build_heuristic_result(issue, mode)
        fallback_result.reasoning = f"{fallback_result.reasoning} 诊断任务异常：{exc}"
        await _update_task(task_id, status="error", result=fallback_result)


def _build_workbench_cards(
    *,
    risk_candidate_count: int,
    overview: StatsOverviewResponse,
    failed_items: list[StatsFailedTableItem],
    stale_items: list[StatsStaleTableItem],
    lookback_days: int,
    stale_days: int,
) -> list[StatsWorkbenchCard]:
    windows = overview.scheduler_windows
    windows_healthy = bool(windows) and all(
        w.enabled and (w.failure_count or 0) == 0 for w in windows
    )
    windows_any_issue = any((not w.enabled) or (w.failure_count or 0) > 0 for w in windows)
    window_status: Literal["healthy", "warning", "critical"] = "healthy"
    if windows and windows_any_issue:
        window_status = "critical"
    elif not windows:
        window_status = "warning"

    cards = [
        StatsWorkbenchCard(
            key="scheduler",
            title="调度健康",
            value="正常" if windows_healthy else "异常" if windows_any_issue else "待确认",
            status=window_status,
            hint=f"启用窗口 {len([w for w in windows if w.enabled])}/{len(windows)}",
        ),
        StatsWorkbenchCard(
            key="failed_tables",
            title="失败表",
            value=str(len(failed_items)),
            status="critical" if failed_items else "healthy",
            hint=f"近 {lookback_days} 天失败表对象",
        ),
        StatsWorkbenchCard(
            key="stale_stats",
            title="过期/缺失统计",
            value=str(len(stale_items)),
            status="warning" if stale_items else "healthy",
            hint=f"近 {stale_days} 天过期/缺失对象",
        ),
        StatsWorkbenchCard(
            key="risk_candidates",
            title="风险候选",
            value=str(risk_candidate_count),
            status="warning" if risk_candidate_count else "healthy",
            hint="失败 / 过期 / 高变化合并视图",
        ),
    ]
    return cards


def _build_issue_queue(
    *,
    overview: StatsOverviewResponse,
    failed_items: list[StatsFailedTableItem],
    stale_items: list[StatsStaleTableItem],
    dml_items: list[StatsDmlChangeItem],
) -> list[StatsIssueItem]:
    issues: list[StatsIssueItem] = []

    windows = overview.scheduler_windows
    windows_by_datasource: dict[tuple[int | None, str | None], list[StatsSchedulerWindow]] = {}
    for window in windows:
        key = (window.datasource_id, window.cluster_key)
        windows_by_datasource.setdefault(key, []).append(window)

    if windows:
        for (
            window_datasource_id,
            window_cluster_key,
        ), scoped_windows in windows_by_datasource.items():
            if any((not w.enabled) or (w.failure_count or 0) > 0 for w in scoped_windows):
                issues.append(
                    StatsIssueItem(
                        issue_id=f"scheduling:{window_datasource_id or 'na'}:windows",
                        kind="scheduling",
                        severity="high",
                        title="调度窗口存在异常",
                        summary="检测到禁用窗口或窗口失败计数大于 0，建议优先检查调度链路。",
                        datasource_id=window_datasource_id,
                        cluster_key=window_cluster_key,
                        facts={
                            "window_total": len(scoped_windows),
                            "window_enabled": len([w for w in scoped_windows if w.enabled]),
                            "window_failed": len(
                                [w for w in scoped_windows if (w.failure_count or 0) > 0]
                            ),
                        },
                    )
                )
    else:
        issues.append(
            StatsIssueItem(
                issue_id="scheduling:missing_windows",
                kind="scheduling",
                severity="medium",
                title="缺少调度窗口事实",
                summary="当前范围未返回维护窗口信息，建议先确认租户权限或监控视图。",
                facts={"missing_fact": "scheduler_windows"},
            )
        )

    for item in failed_items[:60]:
        reason = _safe_text(item.stat_refresh_failed_list, "")
        gather_seconds = item.gather_seconds or 0
        severity: Literal["high", "medium", "low"] = (
            "high"
            if ("timeout" in reason.lower() or "window" in reason.lower() or gather_seconds >= 1800)
            else "medium"
        )
        owner = _safe_text(item.owner, "unknown")
        table_name = _safe_text(item.table_name, "unknown")
        issues.append(
            StatsIssueItem(
                issue_id=f"failed:{item.datasource_id or 'na'}:{owner}.{table_name}:{_safe_text(item.task_start_time, 'latest')}",
                kind="failed_table",
                severity=severity,
                title=f"{owner}.{table_name} 收集失败",
                summary="自动收集任务未成功完成，建议检查失败原因并判断是否属于大表/窗口问题。",
                datasource_id=item.datasource_id,
                cluster_key=item.cluster_key,
                tenant_name=item.tenant_name or item.owner,
                database_name=None,
                table_name=item.table_name,
                facts={
                    "owner": owner,
                    "error_reason": reason,
                    "gather_seconds": gather_seconds,
                    "task_start_time": _safe_text(item.task_start_time),
                    "status": _safe_text(item.status),
                },
            )
        )

    for item in stale_items[:60]:
        state = _safe_text(item.stats_state, "STALE_STATS")
        severity = "high" if state == "MISSING_STATS" else "medium"
        owner = _safe_text(item.owner, "unknown")
        table_name = _safe_text(item.table_name, "unknown")
        issues.append(
            StatsIssueItem(
                issue_id=f"stale:{item.datasource_id or 'na'}:{owner}.{table_name}",
                kind="stale_stats",
                severity=severity,  # type: ignore[arg-type]
                title=f"{owner}.{table_name} 统计信息{'缺失' if state == 'MISSING_STATS' else '过期'}",
                summary="统计信息状态异常，可能影响优化器估算与计划选择。",
                datasource_id=item.datasource_id,
                cluster_key=item.cluster_key,
                tenant_name=item.tenant_name or item.owner,
                database_name=item.owner,
                table_name=item.table_name,
                facts={
                    "stats_state": state,
                    "last_analyzed": _safe_text(item.last_analyzed),
                },
            )
        )

    for item in dml_items[:60]:
        delta = item.row_change_delta or 0
        severity: Literal["high", "medium", "low"] = "high" if delta >= 1_000_000 else "medium"
        db_name = _safe_text(item.database_name, "unknown")
        table_name = _safe_text(item.table_name, "unknown")
        issues.append(
            StatsIssueItem(
                issue_id=f"dml:{item.datasource_id or 'na'}:{db_name}.{table_name}",
                kind="dml_change",
                severity=severity,
                title=f"{db_name}.{table_name} DML 变化显著",
                summary="数据变化量较高，统计信息可能已无法反映当前分布。",
                datasource_id=item.datasource_id,
                cluster_key=item.cluster_key,
                tenant_name=item.tenant_name,
                database_name=item.database_name,
                table_name=item.table_name,
                facts={"row_change_delta": delta},
            )
        )

    deduped: dict[str, StatsIssueItem] = {}
    for issue in issues:
        if issue.issue_id not in deduped:
            deduped[issue.issue_id] = issue
    return sorted(deduped.values(), key=_issue_sort_key, reverse=True)


async def _build_drawer_detail_response(
    ds: models.DataSource,
    *,
    issue: StatsIssueItem,
    db: Session | None = None,
    risk_candidate: StatsRiskCandidateItem | None = None,
) -> StatsDrawerDetailResponse:
    facts = issue.facts or {}
    datasource_tenant_name = _datasource_tenant_name(ds)
    subtitle = (
        " / ".join(
            [
                part
                for part in [
                    ds.cluster_key or None,
                    issue.tenant_name or datasource_tenant_name,
                    issue.database_name,
                    issue.table_name,
                ]
                if part
            ]
        )
        or None
    )

    base_sections = [
        StatsDrawerDetailSection(
            key="scope",
            title="对象范围",
            fields=_build_drawer_fields(
                [
                    ("集群", ds.cluster_key, "datasource"),
                    ("租户", issue.tenant_name or datasource_tenant_name, "issue"),
                    ("表", issue.table_name, "issue"),
                ]
            ),
        ),
    ]

    history_source, history_rows_raw, history_missing = await _fetch_table_gather_history_rows(
        ds,
        table_name=issue.table_name,
        db=db,
    )
    task_context, task_missing = await _fetch_task_history_context(
        ds,
        task_ids=[
            str(row.get("task_id")) for row in history_rows_raw if row.get("task_id") is not None
        ],
    )

    history_rows: list[StatsDrawerHistoryRow] = []
    for row in history_rows_raw:
        task_id = _safe_text(row.get("task_id"), "")
        task_row = task_context.get(task_id, {})
        history_rows.append(
            StatsDrawerHistoryRow(
                task_id=task_id or None,
                owner=str(row.get("owner")).strip()
                if row.get("owner") is not None and str(row.get("owner")).strip()
                else None,
                table_name=str(row.get("table_name")).strip()
                if row.get("table_name") is not None and str(row.get("table_name")).strip()
                else None,
                status=str(row.get("status")).strip()
                if row.get("status") is not None and str(row.get("status")).strip()
                else None,
                ret_code=str(row.get("ret_code")).strip()
                if row.get("ret_code") is not None and str(row.get("ret_code")).strip()
                else None,
                start_time=str(row.get("start_time")).strip()
                if row.get("start_time") is not None and str(row.get("start_time")).strip()
                else None,
                end_time=str(row.get("end_time")).strip()
                if row.get("end_time") is not None and str(row.get("end_time")).strip()
                else None,
                gather_seconds=_int(row, "gather_seconds"),
                memory_used=_int(row, "memory_used"),
                trigger_type=_infer_trigger_type(task_row.get("type")),
                stat_refresh_failed_list=_safe_text(row.get("stat_refresh_failed_list")),
                properties=_safe_text(row.get("properties")),
                task_table_count=_int(task_row, "table_count"),
                task_failed_count=_int(task_row, "failed_count"),
            )
        )

    latest_history = history_rows[0] if history_rows else None
    # Find the latest FAILED row for error info (ret_code != 0 for SYS, status != SUCCESS for tenant)
    latest_failed = next(
        (
            r
            for r in history_rows
            if (r.ret_code and str(r.ret_code) not in ("0", "None", ""))
            or (r.status and r.status != "SUCCESS")
        ),
        None,
    )
    latest_error_reason = _safe_text(facts.get("error_reason"), "")
    latest_error_code = _extract_error_code(
        latest_error_reason,
        latest_failed.ret_code if latest_failed else facts.get("ret_code"),
    )

    # Parse PROPERTIES for display
    latest_props = _parse_properties(latest_history.properties) if latest_history else {}

    task_fields: list[tuple[str, Any, str | None]] = [
        ("触发方式", latest_history.trigger_type if latest_history else None, history_source),
        (
            "开始时间",
            latest_history.start_time if latest_history else facts.get("task_start_time"),
            history_source,
        ),
        (
            "结束时间",
            latest_history.end_time if latest_history else facts.get("task_end_time"),
            history_source,
        ),
        (
            "耗时(秒)",
            latest_history.gather_seconds if latest_history else facts.get("gather_seconds"),
            history_source,
        ),
        (
            "内存使用(bytes)",
            latest_history.memory_used if latest_history else facts.get("memory_used"),
            history_source,
        ),
    ]
    if latest_history and latest_history.task_table_count is not None:
        task_fields.append(
            (
                "任务表数",
                f"{latest_history.task_table_count} (失败 {latest_history.task_failed_count or 0})",
                history_source,
            )
        )
    if latest_props:
        prop_labels = {
            "GRANULARITY": "采集粒度",
            "METHOD_OPT": "采集方法",
            "DEGREE": "并行度",
            "ESTIMATE_PERCENT": "采样率",
        }
        for key, label in prop_labels.items():
            if key in latest_props:
                task_fields.append((label, latest_props[key], history_source))

    task_section = StatsDrawerDetailSection(
        key="task",
        title="最近一次采集",
        fields=_build_drawer_fields(task_fields),
    )

    failed_status = (
        latest_failed.status if latest_failed and latest_failed.status else None
    ) or facts.get("status")
    failed_time = latest_failed.start_time if latest_failed else facts.get("task_start_time")

    failure_section = StatsDrawerDetailSection(
        key="failure",
        title="失败信息",
        fields=_build_drawer_fields(
            [
                ("最近失败时间", failed_time, history_source),
                ("状态", failed_status, history_source),
                ("错误码", latest_error_code if latest_error_code != "-" else None, history_source),
                ("DML 变化量", facts.get("row_change_delta"), "issue"),
                ("统计状态", facts.get("stats_state"), "issue"),
            ]
        ),
    )

    tags = []
    if risk_candidate is not None:
        tags = [
            f"{tag.tag_label} ({tag.severity}/{round(float(tag.score or 0.0), 2)})"
            for tag in risk_candidate.tags
        ]
    elif isinstance(facts.get("tags"), list):
        tags = [
            _safe_text((tag or {}).get("tag_label") or (tag or {}).get("tag_key"))
            for tag in facts.get("tags")
            if isinstance(tag, dict)
        ]
    tag_section = StatsDrawerDetailSection(
        key="risk-tags",
        title="风险标签",
        fields=_build_drawer_fields([("标签", " / ".join(tags), "risk_candidate")]),
    )

    sections = [
        section
        for section in [*base_sections, task_section, failure_section, tag_section]
        if section.fields
    ]
    missing_facts = _merge_missing_facts(history_missing, task_missing)

    chat_context = {
        "issue_id": issue.issue_id,
        "issue_kind": issue.kind,
        "severity": issue.severity,
        "datasource": {
            "id": ds.id,
            "name": ds.name,
            "cluster_key": ds.cluster_key,
            "tenant_role": ds.tenant_role,
        },
        "tenant_name": issue.tenant_name or datasource_tenant_name,
        "database_name": issue.database_name,
        "table_name": issue.table_name,
        "error_code": latest_error_code,
        "error_reason": latest_error_reason or issue.summary,
        "history_source": history_source,
        "history_sample_count": len(history_rows),
        "missing_facts": missing_facts,
        "tags": tags,
    }

    return StatsDrawerDetailResponse(
        datasource_id=ds.id,
        title=issue.title,
        object_kind=issue.kind,
        severity=issue.severity,
        summary=issue.summary,
        subtitle=subtitle,
        sections=sections,
        history_rows=history_rows,
        history_source=history_source,
        missing_facts=missing_facts,
        chat_context=chat_context,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/workbench", response_model=StatsWorkbenchResponse)
async def get_stats_workbench(
    datasource_id: int | None = Query(None, ge=1),
    cluster_key: str | None = Query(None),
    lookback_days: int = Query(7, ge=1, le=90),
    stale_days: int = Query(7, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """工作台聚合：概况 + 问题队列。支持多租户/多集群聚合。"""
    targets = _resolve_datasources(db, datasource_id, cluster_key)
    if not targets:
        return StatsWorkbenchResponse(
            datasource_id=datasource_id,
            cluster_key=cluster_key or "all",
            overview=StatsOverviewResponse(
                task_summary=StatsTaskSummary(
                    total_tasks=0,
                    success_tasks=0,
                    failed_tasks=0,
                    failed_task_ratio_pct=0.0,
                    total_tables_planned=0,
                    total_tables_failed=0,
                ),
                scheduler_windows=[],
            ),
            cards=[],
            issues=[],
            warnings=["未找到可用数据源，请先在数据源管理中添加数据源。"],
            tenant_config_checks=[],
        )

    effective_cluster = (
        _safe_text(cluster_key, "") if cluster_key else _safe_text(targets[0].cluster_key, "")
    )

    logger.info(
        "stats_workbench_start datasource_id=%s cluster_key=%s targets=%d lookback=%s stale=%s",
        datasource_id,
        cluster_key,
        len(targets),
        lookback_days,
        stale_days,
    )

    per_target = await asyncio.gather(
        *[
            asyncio.gather(
                get_stats_overview(datasource_id=ds.id, lookback_days=lookback_days, db=db),
                get_failed_tables(datasource_id=ds.id, lookback_days=lookback_days, db=db),
                get_stale_tables(datasource_id=ds.id, stale_days=stale_days, db=db),
                get_dml_changes(datasource_id=ds.id, db=db),
            )
            for ds in targets
        ]
    )

    merged_total_tasks = 0
    merged_success_tasks = 0
    merged_failed_tasks = 0
    merged_total_tables_planned = 0
    merged_total_tables_failed = 0
    merged_scheduler_windows: list[StatsSchedulerWindow] = []
    merged_failed_items: list[StatsFailedTableItem] = []
    merged_stale_items: list[StatsStaleTableItem] = []
    merged_dml_items: list[StatsDmlChangeItem] = []

    for overview, failed, stale, dml in per_target:
        merged_total_tasks += overview.task_summary.total_tasks
        merged_success_tasks += overview.task_summary.success_tasks
        merged_failed_tasks += overview.task_summary.failed_tasks
        merged_total_tables_planned += overview.task_summary.total_tables_planned
        merged_total_tables_failed += overview.task_summary.total_tables_failed
        merged_scheduler_windows.extend(overview.scheduler_windows)
        merged_failed_items.extend(failed.items)
        merged_stale_items.extend(stale.items)
        merged_dml_items.extend(dml.items)

    failed_task_ratio_pct = (
        round((100 * merged_failed_tasks / merged_total_tasks), 2) if merged_total_tasks else 0.0
    )
    overview = StatsOverviewResponse(
        task_summary=StatsTaskSummary(
            total_tasks=merged_total_tasks,
            success_tasks=merged_success_tasks,
            failed_tasks=merged_failed_tasks,
            failed_task_ratio_pct=failed_task_ratio_pct,
            total_tables_planned=merged_total_tables_planned,
            total_tables_failed=merged_total_tables_failed,
        ),
        scheduler_windows=merged_scheduler_windows,
    )

    risk_candidate_count = sum(_count_active_risk_candidates(db, ds.id) for ds in targets)
    cards = _build_workbench_cards(
        risk_candidate_count=risk_candidate_count,
        overview=overview,
        failed_items=merged_failed_items,
        stale_items=merged_stale_items,
        lookback_days=lookback_days,
        stale_days=stale_days,
    )
    issues = _build_issue_queue(
        overview=overview,
        failed_items=merged_failed_items,
        stale_items=merged_stale_items,
        dml_items=merged_dml_items,
    )
    warnings: list[str] = []
    if not overview.scheduler_windows:
        warnings.append("当前范围缺少 scheduler window 事实，将以降级模式组织调度诊断。")

    cluster_keys_to_scan: list[str] = []
    if cluster_key:
        cluster_keys_to_scan = [cluster_key]
    elif datasource_id:
        cluster_keys_to_scan = list(
            {_safe_text(ds.cluster_key, "") for ds in targets if ds.cluster_key}
        )
    else:
        cluster_keys_to_scan = list(
            {_safe_text(ds.cluster_key, "") for ds in targets if ds.cluster_key}
        )
    tenant_config_checks: list[StatsTenantConfigCheck] = []
    for ck in cluster_keys_to_scan:
        try:
            tenant_config_checks.extend(
                await _check_tenant_configs(db, ck, lookback_days, datasource_id=datasource_id)
            )
        except Exception as exc:
            logger.warning("stats_workbench tenant_config_checks failed cluster=%s: %s", ck, exc)

    return StatsWorkbenchResponse(
        datasource_id=datasource_id,
        cluster_key=cluster_key or effective_cluster or "all",
        overview=overview,
        cards=cards,
        issues=issues,
        warnings=warnings,
        tenant_config_checks=tenant_config_checks,
    )


@router.post("/risk-candidates/collect", response_model=StatsRiskCollectResponse)
async def collect_stats_risk_candidates(
    payload: StatsRiskCollectRequest, db: Session = Depends(get_db)
):
    _get_datasource(db, payload.datasource_id)
    schedule = _ensure_stats_analysis_schedule(db)
    schedule.input_payload = {
        "datasource_id": payload.datasource_id,
        "lookback_days": payload.lookback_days,
        "stale_days": payload.stale_days,
        "trigger_type": "manual",
    }
    db.add(schedule)
    db.commit()

    trace_id = str(uuid.uuid4())
    run_id = ""
    worker = get_scheduler_worker()
    if worker is not None and worker.health().get("running"):
        run_id = await worker.run_now(schedule.id, trace_id=trace_id)
    else:
        from sqlalchemy.orm import sessionmaker

        from app.services.function.runtime import FunctionRuntimeService
        from app.services.scheduler.worker import SchedulerWorker

        runtime_session_factory = sessionmaker(
            bind=db.get_bind(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        fallback_worker = SchedulerWorker(
            session_factory=runtime_session_factory,
            runtime_service=FunctionRuntimeService(session_factory=runtime_session_factory),
        )
        try:
            run_id = await fallback_worker.run_now(schedule.id, trace_id=trace_id)
        finally:
            await fallback_worker.shutdown()

    run = (
        db.query(models.ScheduleRun)
        .filter(
            models.ScheduleRun.schedule_id == schedule.id,
            models.ScheduleRun.run_id == run_id,
        )
        .first()
    )
    payload_obj = (run.output_payload if run else None) or {}
    if isinstance(payload_obj, dict) and "collected_tables" in payload_obj:
        result = StatsRiskCollectResponse.model_validate(payload_obj)
    else:
        result = _build_collect_response_from_db(
            db,
            payload.datasource_id,
            fallback_collected=int((payload_obj or {}).get("collected_tables") or 0),
        )
    current_active = (
        db.query(models.StatsRiskCandidate)
        .filter(
            models.StatsRiskCandidate.datasource_id == payload.datasource_id,
            models.StatsRiskCandidate.lifecycle_status == "active",
        )
        .count()
    )
    if current_active == 0:
        # Fallback to deterministic local collect when schedule runtime produced no visible candidate.
        result = await _collect_risk_candidates_once(
            db,
            datasource_id=payload.datasource_id,
            lookback_days=payload.lookback_days,
            stale_days=payload.stale_days,
            trigger_type="manual",
        )
    return result


@router.get("/risk-candidates", response_model=StatsRiskCandidatesResponse)
async def list_stats_risk_candidates(
    datasource_id: int = Query(..., ge=1),
    auto_collect: bool = Query(False),
    include_inactive: bool = Query(False),
    lifecycle_status: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    ds = _get_datasource(db, datasource_id)
    datasource_tenant_name = _datasource_tenant_name(ds)
    _ = auto_collect  # backward compatibility; schedule-driven collection is the primary path.
    _ensure_stats_analysis_schedule(db)
    query = db.query(models.StatsRiskCandidate).filter(
        models.StatsRiskCandidate.datasource_id == datasource_id
    )
    if lifecycle_status:
        query = query.filter(models.StatsRiskCandidate.lifecycle_status == lifecycle_status)
    elif not include_inactive:
        query = query.filter(models.StatsRiskCandidate.lifecycle_status == "active")
    candidates = query.limit(limit).all()
    items = sorted(
        [_to_risk_candidate_item(candidate) for candidate in candidates],
        key=lambda item: (
            _SEVERITY_SCORE.get(item.severity, 0),
            item.score,
            item.last_seen_at,
        ),
        reverse=True,
    )
    if datasource_tenant_name:
        for item in items:
            item.tenant_name = datasource_tenant_name
    return StatsRiskCandidatesResponse(datasource_id=datasource_id, items=items)


@router.get("/risk-candidates/collect-runs", response_model=StatsRiskCollectionRunsResponse)
async def list_stats_risk_collection_runs(
    datasource_id: int = Query(..., ge=1),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    _get_datasource(db, datasource_id)
    schedule = _ensure_stats_analysis_schedule(db)
    runs = (
        db.query(models.ScheduleRun)
        .filter(models.ScheduleRun.schedule_id == schedule.id)
        .order_by(models.ScheduleRun.created_at.desc())
        .limit(max(limit * 8, 64))
        .all()
    )
    items: list[StatsRiskCollectionRunItem] = []
    for run in runs:
        payload_obj = run.output_payload if isinstance(run.output_payload, dict) else {}
        run_datasource_id = payload_obj.get("datasource_id")
        if run_datasource_id == datasource_id:
            items.append(
                StatsRiskCollectionRunItem(
                    run_id=run.run_id,
                    datasource_id=datasource_id,
                    trigger_type=run.trigger_type,
                    status=run.status,
                    summary=run.output_summary,
                    error_summary=run.error_summary,
                    started_at=_dt_to_iso(run.started_at) if run.started_at else None,
                    finished_at=_dt_to_iso(run.finished_at) if run.finished_at else None,
                )
            )
            if len(items) >= limit:
                break
            continue
        batch_items = payload_obj.get("items")
        if not isinstance(batch_items, list):
            continue
        if not any(
            isinstance(item, dict) and item.get("datasource_id") == datasource_id
            for item in batch_items
        ):
            continue
        items.append(
            StatsRiskCollectionRunItem(
                run_id=run.run_id,
                datasource_id=datasource_id,
                trigger_type=run.trigger_type,
                status=run.status,
                summary=run.output_summary,
                error_summary=run.error_summary,
                started_at=_dt_to_iso(run.started_at) if run.started_at else None,
                finished_at=_dt_to_iso(run.finished_at) if run.finished_at else None,
            )
        )
        if len(items) >= limit:
            break
    return StatsRiskCollectionRunsResponse(datasource_id=datasource_id, items=items)


@router.get("/risk-candidates/{candidate_id}", response_model=StatsRiskCandidateItem)
async def get_stats_risk_candidate(
    candidate_id: int,
    datasource_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
):
    ds = _get_datasource(db, datasource_id)
    datasource_tenant_name = _datasource_tenant_name(ds)
    candidate = (
        db.query(models.StatsRiskCandidate)
        .filter(
            models.StatsRiskCandidate.id == candidate_id,
            models.StatsRiskCandidate.datasource_id == datasource_id,
        )
        .first()
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Risk candidate not found")
    item = _to_risk_candidate_item(candidate)
    if datasource_tenant_name:
        item.tenant_name = datasource_tenant_name
    return item


@router.post("/drawer-detail", response_model=StatsDrawerDetailResponse)
async def get_stats_drawer_detail(
    payload: StatsDrawerDetailRequest,
    db: Session = Depends(get_db),
):
    ds = _get_datasource(db, payload.datasource_id)
    issue = payload.issue
    if issue is None and payload.risk_candidate is not None:
        issue = _build_issue_from_risk_candidate_payload(payload.risk_candidate)
    if issue is None:
        raise HTTPException(status_code=400, detail="issue or risk_candidate is required")
    logger.info(
        "stats_drawer_detail_start datasource_id=%s issue=%s kind=%s",
        payload.datasource_id,
        issue.issue_id,
        issue.kind,
    )
    response = await _build_drawer_detail_response(
        ds,
        issue=issue,
        risk_candidate=payload.risk_candidate,
        db=db,
    )
    logger.info(
        "stats_drawer_detail_success datasource_id=%s issue=%s history_source=%s missing=%s",
        payload.datasource_id,
        issue.issue_id,
        response.history_source,
        len(response.missing_facts),
    )
    return response


@router.post(
    "/risk-candidates/{candidate_id}/analysis", response_model=StatsRiskAnalyzeSubmitResponse
)
async def submit_stats_risk_analysis(
    candidate_id: int,
    datasource_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
):
    _get_datasource(db, datasource_id)
    candidate = (
        db.query(models.StatsRiskCandidate)
        .filter(
            models.StatsRiskCandidate.id == candidate_id,
            models.StatsRiskCandidate.datasource_id == datasource_id,
        )
        .first()
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Risk candidate not found")
    run_id = f"risk-{uuid.uuid4().hex}"
    run = models.StatsRiskAnalysisRun(
        run_id=run_id,
        datasource_id=datasource_id,
        candidate_id=candidate.id,
        status="pending",
        trigger_type="manual",
    )
    db.add(run)
    try:
        await _run_candidate_analysis_in_session(db, run)
        db.commit()
    except Exception as exc:
        db.rollback()
        run.status = "error"
        run.error_summary = str(exc)
        run.finished_at = datetime.utcnow()
        db.add(run)
        db.commit()
    status = run.status
    logger.info(
        "stats_risk_analysis_submit run_id=%s datasource_id=%s candidate_id=%s",
        run_id,
        datasource_id,
        candidate_id,
    )
    return StatsRiskAnalyzeSubmitResponse(run_id=run_id, status=status)


@router.post("/risk-candidates/{candidate_id}/analysis/stream")
async def stream_stats_risk_analysis(
    candidate_id: int,
    datasource_id: int = Query(..., ge=1),
):
    def _to_sse(event: dict[str, Any]) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    async def generate():
        db = SessionLocal()
        run: models.StatsRiskAnalysisRun | None = None
        try:
            _get_datasource(db, datasource_id)
            candidate = (
                db.query(models.StatsRiskCandidate)
                .filter(
                    models.StatsRiskCandidate.id == candidate_id,
                    models.StatsRiskCandidate.datasource_id == datasource_id,
                )
                .first()
            )
            if candidate is None:
                yield _to_sse(
                    {
                        "type": "error",
                        "data": {"message": "Risk candidate not found"},
                    }
                )
                return

            run_id = f"risk-{uuid.uuid4().hex}"
            run = models.StatsRiskAnalysisRun(
                run_id=run_id,
                datasource_id=datasource_id,
                candidate_id=candidate.id,
                status="pending",
                trigger_type="manual",
            )
            db.add(run)
            db.commit()
            yield _to_sse(
                {
                    "type": "phase",
                    "data": {"phase": "submitted", "run_id": run_id, "status": "pending"},
                }
            )

            run.status = "running"
            run.started_at = datetime.utcnow()
            db.commit()
            yield _to_sse(
                {
                    "type": "phase",
                    "data": {"phase": "collecting_context", "run_id": run_id, "status": "running"},
                }
            )

            issue = _build_issue_from_candidate(candidate)
            extra_facts, extra_evidence, upstream_missing_facts = await _collect_deep_issue_context(
                datasource_id, issue
            )
            yield _to_sse(
                {
                    "type": "phase",
                    "data": {
                        "phase": "reasoning",
                        "run_id": run_id,
                        "status": "running",
                    },
                }
            )

            stream_queue: asyncio.Queue[str | None] = asyncio.Queue()

            async def _on_delta(text: str) -> None:
                await stream_queue.put(text)

            llm_task = asyncio.create_task(
                _call_llm_for_diagnosis(
                    issue=issue,
                    mode="deep",
                    extra_facts=extra_facts,
                    extra_evidence=extra_evidence,
                    missing_facts=upstream_missing_facts,
                    on_delta=_on_delta,
                )
            )

            while True:
                if llm_task.done() and stream_queue.empty():
                    break
                try:
                    chunk = await asyncio.wait_for(stream_queue.get(), timeout=0.2)
                except TimeoutError:
                    continue
                if chunk:
                    yield _to_sse({"type": "delta", "data": {"run_id": run_id, "chunk": chunk}})

            llm_result = await llm_task
            if llm_result is None:
                status, result = _build_heuristic_result(
                    issue,
                    "deep",
                    extra_facts=extra_facts,
                    extra_evidence=extra_evidence,
                    upstream_missing_facts=upstream_missing_facts,
                )
            else:
                llm_result.evidence = _merge_evidence(llm_result.evidence, extra_evidence)
                llm_result.missing_facts = _merge_missing_facts(
                    llm_result.missing_facts, upstream_missing_facts
                )
                status = "needs_clarification" if llm_result.missing_facts else "ready"
                result = llm_result

            run.status = status
            run.summary = result.headline
            run.result_payload = result.model_dump()
            run.error_summary = None
            run.finished_at = datetime.utcnow()
            db.commit()
            yield _to_sse(
                {
                    "type": "done",
                    "data": {
                        "run_id": run_id,
                        "status": status,
                        "result": result.model_dump(),
                    },
                }
            )
        except Exception as exc:
            db.rollback()
            logger.exception(
                "stats_risk_analysis_stream_failed datasource_id=%s candidate_id=%s error=%s",
                datasource_id,
                candidate_id,
                exc,
            )
            if run is not None:
                run.status = "error"
                run.error_summary = str(exc)
                run.finished_at = datetime.utcnow()
                db.add(run)
                db.commit()
            yield _to_sse({"type": "error", "data": {"message": str(exc)}})
        finally:
            db.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/risk-candidates/analysis/{run_id}", response_model=StatsRiskAnalyzeStatusResponse)
async def get_stats_risk_analysis(run_id: str, db: Session = Depends(get_db)):
    run = (
        db.query(models.StatsRiskAnalysisRun)
        .filter(models.StatsRiskAnalysisRun.run_id == run_id)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Risk analysis run not found")
    payload = run.result_payload or {}
    result = None
    if isinstance(payload, dict) and payload:
        result = StatsDiagnosisResult.model_validate(payload)
    return StatsRiskAnalyzeStatusResponse(
        run_id=run_id,
        status=run.status,  # type: ignore[arg-type]
        result=result,
        error_summary=run.error_summary,
    )


@router.post("/diagnosis", response_model=StatsDiagnosisTaskSubmitResponse)
async def submit_stats_diagnosis(payload: StatsDiagnosisRequest):
    """提交异步诊断任务（summary/deep）。"""
    task_id = f"diag-{uuid.uuid4().hex}"
    await _update_task(task_id, status="pending")
    logger.info(
        "stats_diagnosis_submit task_id=%s datasource_id=%s mode=%s issue=%s",
        task_id,
        payload.datasource_id,
        payload.mode,
        payload.issue.issue_id,
    )
    asyncio.create_task(
        _run_diagnosis_task(
            task_id,
            datasource_id=payload.datasource_id,
            issue=payload.issue,
            mode=payload.mode,
        )
    )
    return StatsDiagnosisTaskSubmitResponse(task_id=task_id, status="pending")


@router.post("/diagnosis/stream")
async def stream_stats_diagnosis(payload: StatsDiagnosisRequest):
    def _to_sse(event: dict[str, Any]) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    async def generate():
        task_id = f"diag-{uuid.uuid4().hex}"
        await _update_task(task_id, status="pending")
        yield _to_sse(
            {
                "type": "phase",
                "data": {"phase": "submitted", "task_id": task_id, "status": "pending"},
            }
        )
        try:
            await _update_task(task_id, status="running")
            yield _to_sse(
                {
                    "type": "phase",
                    "data": {
                        "phase": "collecting_context",
                        "task_id": task_id,
                        "status": "running",
                    },
                }
            )
            extra_facts: dict[str, Any] = {}
            extra_evidence: list[StatsDiagnosisEvidence] = []
            upstream_missing_facts: list[str] = []
            if payload.mode == "deep":
                (
                    extra_facts,
                    extra_evidence,
                    upstream_missing_facts,
                ) = await _collect_deep_issue_context(
                    payload.datasource_id,
                    payload.issue,
                )
            yield _to_sse(
                {
                    "type": "phase",
                    "data": {"phase": "reasoning", "task_id": task_id, "status": "running"},
                }
            )
            queue: asyncio.Queue[str] = asyncio.Queue()

            async def _on_delta(text: str) -> None:
                await queue.put(text)

            llm_task = asyncio.create_task(
                _call_llm_for_diagnosis(
                    payload.issue,
                    payload.mode,
                    extra_facts=extra_facts,
                    extra_evidence=extra_evidence,
                    missing_facts=upstream_missing_facts,
                    on_delta=_on_delta,
                )
            )
            while True:
                if llm_task.done() and queue.empty():
                    break
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=0.2)
                except TimeoutError:
                    continue
                if chunk:
                    yield _to_sse({"type": "delta", "data": {"task_id": task_id, "chunk": chunk}})

            llm_result = await llm_task
            if llm_result is not None:
                llm_result.evidence = _merge_evidence(llm_result.evidence, extra_evidence)
                llm_result.missing_facts = _merge_missing_facts(
                    llm_result.missing_facts, upstream_missing_facts
                )
                status: Literal["ready", "degraded", "needs_clarification"] = (
                    "needs_clarification"
                    if llm_result.missing_facts and payload.mode == "deep"
                    else "degraded"
                    if llm_result.missing_facts
                    else "ready"
                )
                await _update_task(task_id, status=status, result=llm_result)
                yield _to_sse(
                    {
                        "type": "done",
                        "data": {
                            "task_id": task_id,
                            "status": status,
                            "result": llm_result.model_dump(),
                        },
                    }
                )
                return

            fallback_status, fallback_result = _build_heuristic_result(
                payload.issue,
                payload.mode,
                extra_facts=extra_facts,
                extra_evidence=extra_evidence,
                upstream_missing_facts=upstream_missing_facts,
            )
            await _update_task(task_id, status=fallback_status, result=fallback_result)
            yield _to_sse(
                {
                    "type": "done",
                    "data": {
                        "task_id": task_id,
                        "status": fallback_status,
                        "result": fallback_result.model_dump(),
                    },
                }
            )
        except Exception as exc:
            _, fallback_result = _build_heuristic_result(payload.issue, payload.mode)
            fallback_result.reasoning = f"{fallback_result.reasoning} 诊断任务异常：{exc}"
            await _update_task(task_id, status="error", result=fallback_result)
            logger.exception(
                "stats_diagnosis_stream_failed task_id=%s mode=%s issue=%s error=%s",
                task_id,
                payload.mode,
                payload.issue.issue_id,
                exc,
            )
            yield _to_sse({"type": "error", "data": {"task_id": task_id, "message": str(exc)}})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/diagnosis/{task_id}", response_model=StatsDiagnosisTaskStatusResponse)
async def get_stats_diagnosis_task(task_id: str):
    async with _DIAG_TASKS_LOCK:
        record = _DIAG_TASKS.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="Diagnosis task not found")
    result = record.get("result")
    return StatsDiagnosisTaskStatusResponse(
        task_id=task_id,
        status=record.get("status", "error"),
        result=result,
    )


@router.get("/overview", response_model=StatsOverviewResponse)
async def get_stats_overview(
    datasource_id: int = Query(..., ge=1),
    tenant_name: str | None = None,
    lookback_days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """任务健康度概览 + 调度窗口状态（L1 §1.2 + §1.3）"""
    ds = _get_datasource(db, datasource_id)
    pool = get_db_pool()

    # --- task summary ---
    sql_task_summary = f"""
SELECT
  COUNT(*) AS total_tasks,
  SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) AS success_tasks,
  SUM(CASE WHEN status != 'SUCCESS' THEN 1 ELSE 0 END) AS failed_tasks,
  ROUND(
    100 * SUM(CASE WHEN status != 'SUCCESS' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
    2
  ) AS failed_task_ratio_pct,
  SUM(table_count) AS total_tables_planned,
  SUM(failed_count) AS total_tables_failed
FROM oceanbase.DBA_OB_TASK_OPT_STAT_GATHER_HISTORY
WHERE type = 'AUTO GATHER'
  AND start_time > DATE_SUB(NOW(), INTERVAL {lookback_days} DAY)
"""
    try:
        result = await pool.execute_query(ds, sql_task_summary)
        rows = _rows_to_dicts(result)
        row = rows[0] if rows else {}
    except Exception as exc:
        logger.warning("stats_overview task_summary failed: %s", exc)
        row = {}

    task_summary = StatsTaskSummary(
        total_tasks=_int(row, "total_tasks") or 0,
        success_tasks=_int(row, "success_tasks") or 0,
        failed_tasks=_int(row, "failed_tasks") or 0,
        failed_task_ratio_pct=_float(row, "failed_task_ratio_pct") or 0.0,
        total_tables_planned=_int(row, "total_tables_planned") or 0,
        total_tables_failed=_int(row, "total_tables_failed") or 0,
    )

    # --- scheduler windows ---
    sql_windows = """
SELECT
  job_name,
  enabled,
  last_start_date,
  next_run_date,
  failure_count
FROM oceanbase.DBA_SCHEDULER_JOBS
WHERE job_name IN (
  'MONDAY_WINDOW','TUESDAY_WINDOW','WEDNESDAY_WINDOW',
  'THURSDAY_WINDOW','FRIDAY_WINDOW','SATURDAY_WINDOW','SUNDAY_WINDOW'
)
ORDER BY job_name
"""
    try:
        result = await pool.execute_query(ds, sql_windows)
        win_rows = _rows_to_dicts(result)
    except Exception as exc:
        logger.warning("stats_overview scheduler_windows failed: %s", exc)
        win_rows = []

    scheduler_windows = [
        StatsSchedulerWindow(
            job_name=_str(r, "job_name") or "",
            enabled=_bool_enabled(_val(r, "enabled")),
            last_start_date=_str(r, "last_start_date"),
            next_run_date=_str(r, "next_run_date"),
            failure_count=_int(r, "failure_count"),
            datasource_id=ds.id,
            cluster_key=ds.cluster_key,
        )
        for r in win_rows
    ]

    return StatsOverviewResponse(task_summary=task_summary, scheduler_windows=scheduler_windows)


@router.get("/failed-tables", response_model=StatsFailedTablesResponse)
async def get_failed_tables(
    datasource_id: int = Query(..., ge=1),
    tenant_name: str | None = None,
    lookback_days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """统计收集失败表明细（L1 §2.1，包含 AUTO/MANUAL GATHER）"""
    ds = _get_datasource(db, datasource_id)
    pool = get_db_pool()
    datasource_tenant_name = _datasource_tenant_name(ds)

    tenant_filter = ""
    if tenant_name:
        tenant_filter = f"AND t_opt.owner = '{tenant_name}'"

    sql = f"""
SELECT
  NULL AS tenant_name,
  t_opt.owner,
  t_opt.table_name,
  task_opt.start_time AS task_start_time,
  task_opt.end_time AS task_end_time,
  TIMESTAMPDIFF(SECOND, t_opt.start_time, t_opt.end_time) AS gather_seconds,
  t_opt.memory_used,
  t_opt.stat_refresh_failed_list,
  t_opt.status
FROM (
  SELECT task_id, start_time, end_time
  FROM oceanbase.DBA_OB_TASK_OPT_STAT_GATHER_HISTORY
  WHERE UPPER(COALESCE(type, '')) LIKE '%GATHER%'
    AND start_time > DATE_SUB(NOW(), INTERVAL {lookback_days} DAY)
) task_opt
JOIN oceanbase.DBA_OB_TABLE_OPT_STAT_GATHER_HISTORY t_opt
  ON task_opt.task_id = t_opt.task_id
WHERE t_opt.status != 'SUCCESS'
  AND t_opt.owner != 'oceanbase'
  {tenant_filter}
ORDER BY task_opt.start_time DESC, gather_seconds DESC
LIMIT 200
"""
    try:
        result = await pool.execute_query(ds, sql)
        rows = _rows_to_dicts(result)
    except Exception as exc:
        logger.warning("stats failed_tables query failed: %s", exc)
        rows = []

    items = [
        StatsFailedTableItem(
            tenant_name=datasource_tenant_name or _str(r, "tenant_name") or _str(r, "owner"),
            owner=_str(r, "owner"),
            table_name=_str(r, "table_name"),
            task_start_time=_str(r, "task_start_time"),
            task_end_time=_str(r, "task_end_time"),
            gather_seconds=_int(r, "gather_seconds"),
            memory_used=_int(r, "memory_used"),
            stat_refresh_failed_list=_str(r, "stat_refresh_failed_list"),
            status=_str(r, "status"),
            datasource_id=ds.id,
            cluster_key=ds.cluster_key,
        )
        for r in rows
    ]
    return StatsFailedTablesResponse(items=items)


@router.get("/stale-tables", response_model=StatsStaleTablesResponse)
async def get_stale_tables(
    datasource_id: int = Query(..., ge=1),
    tenant_name: str | None = None,
    stale_days: int = Query(7, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """长期未更新统计信息的表（L1 §2.2）"""
    ds = _get_datasource(db, datasource_id)
    pool = get_db_pool()
    datasource_tenant_name = _datasource_tenant_name(ds)

    owner_filter = ""
    if tenant_name:
        owner_filter = f"AND owner = '{tenant_name}'"

    sql = f"""
SELECT
  owner AS tenant_name,
  owner,
  table_name,
  last_analyzed,
  CASE
    WHEN last_analyzed IS NULL THEN 'MISSING_STATS'
    WHEN last_analyzed < DATE_SUB(NOW(), INTERVAL {stale_days} DAY) THEN 'STALE_STATS'
    ELSE 'FRESH'
  END AS stats_state
FROM oceanbase.DBA_TAB_STATISTICS
WHERE owner != 'oceanbase'
  AND (
    last_analyzed IS NULL
    OR last_analyzed < DATE_SUB(NOW(), INTERVAL {stale_days} DAY)
  )
  {owner_filter}
ORDER BY (last_analyzed IS NULL) DESC, last_analyzed ASC
LIMIT 200
"""
    try:
        result = await pool.execute_query(ds, sql)
        rows = _rows_to_dicts(result)
    except Exception as exc:
        logger.warning("stats stale_tables query failed: %s", exc)
        rows = []

    items = [
        StatsStaleTableItem(
            tenant_name=datasource_tenant_name or _str(r, "tenant_name") or _str(r, "owner"),
            owner=_str(r, "owner"),
            table_name=_str(r, "table_name"),
            last_analyzed=_str(r, "last_analyzed"),
            stats_state=_str(r, "stats_state"),
            datasource_id=ds.id,
            cluster_key=ds.cluster_key,
        )
        for r in rows
    ]
    return StatsStaleTablesResponse(items=items)


@router.get("/dml-changes", response_model=StatsDmlChangesResponse)
async def get_dml_changes(
    datasource_id: int = Query(..., ge=1),
    tenant_name: str | None = None,
    db: Session = Depends(get_db),
):
    """DML 变化量排序的 stale 候选表（L1 §2.3）"""
    ds = _get_datasource(db, datasource_id)
    pool = get_db_pool()
    datasource_tenant_name = _datasource_tenant_name(ds)

    db_filter = ""
    if tenant_name:
        db_filter = f"AND s.database_name = '{tenant_name}'"

    sql = f"""
SELECT
  s.database_name AS tenant_name,
  s.database_name,
  s.table_name,
  SUM(m.inserts - m.deletes) AS row_change_delta
FROM oceanbase.DBA_TAB_MODIFICATIONS m
JOIN (
  SELECT DISTINCT database_name, table_name
  FROM oceanbase.DBA_OB_TABLE_STAT_STALE_INFO
  WHERE is_stale = 'YES'
    AND database_name != 'oceanbase'
) s ON m.table_name = s.table_name
{db_filter}
GROUP BY s.database_name, s.table_name
ORDER BY row_change_delta DESC
LIMIT 200
"""
    try:
        result = await pool.execute_query(ds, sql)
        rows = _rows_to_dicts(result)
    except Exception as exc:
        logger.warning("stats dml_changes query failed: %s", exc)
        rows = []

    items = [
        StatsDmlChangeItem(
            tenant_name=datasource_tenant_name
            or _str(r, "tenant_name")
            or _str(r, "database_name"),
            database_name=_str(r, "database_name"),
            table_name=_str(r, "table_name"),
            row_change_delta=_int(r, "row_change_delta"),
            datasource_id=ds.id,
            cluster_key=ds.cluster_key,
        )
        for r in rows
    ]
    return StatsDmlChangesResponse(items=items)


@router.get("/trend", response_model=StatsTrendResponse)
async def get_stats_trend(
    datasource_id: int = Query(..., ge=1),
    lookback_days: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
):
    """收集任务近 N 天趋势：每天平均/最大耗时 + 失败表数（按 DBA_OB_TASK_OPT_STAT_GATHER_HISTORY 聚合）"""
    ds = _get_datasource(db, datasource_id)
    pool = get_db_pool()

    sql = f"""
SELECT
  DATE(start_time) AS trend_date,
  ROUND(AVG(TIMESTAMPDIFF(SECOND, start_time, end_time)) / 60.0, 2) AS avg_duration_min,
  ROUND(MAX(TIMESTAMPDIFF(SECOND, start_time, end_time)) / 60.0, 2) AS max_duration_min,
  COALESCE(SUM(failed_count), 0) AS failed_tables,
  COUNT(*) AS total_tasks
FROM oceanbase.DBA_OB_TASK_OPT_STAT_GATHER_HISTORY
WHERE type = 'AUTO GATHER'
  AND start_time >= DATE_SUB(CURDATE(), INTERVAL {lookback_days} DAY)
  AND end_time IS NOT NULL
GROUP BY DATE(start_time)
ORDER BY trend_date ASC
"""
    try:
        result = await pool.execute_query(ds, sql)
        rows = _rows_to_dicts(result)
    except Exception as exc:
        logger.warning("stats trend query failed: %s", exc)
        rows = []

    points = [
        StatsTrendPoint(
            date=str(_val(r, "trend_date", "")),
            avg_duration_min=_float(r, "avg_duration_min") or 0.0,
            max_duration_min=_float(r, "max_duration_min") or 0.0,
            failed_tables=_int(r, "failed_tables") or 0,
            total_tasks=_int(r, "total_tasks") or 0,
        )
        for r in rows
    ]
    return StatsTrendResponse(points=points)


@router.get("/daily-collection-summary", response_model=StatsCollectionDailySummaryResponse)
async def get_daily_collection_summary(
    datasource_id: int | None = Query(None, ge=1),
    cluster_key: str | None = Query(None),
    lookback_days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """按天汇总统计信息收集任务：每天任务数/成功/失败/表数/耗时。支持多租户聚合。"""
    targets = _resolve_datasources(db, datasource_id, cluster_key)
    if not targets:
        return StatsCollectionDailySummaryResponse(datasource_id=datasource_id, items=[])
    pool = get_db_pool()

    task_sql = f"""
SELECT
  DATE(start_time) AS task_date,
  GROUP_CONCAT(DISTINCT type SEPARATOR ', ') AS task_types,
  COUNT(*) AS total_tasks,
  SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) AS success_tasks,
  SUM(CASE WHEN status != 'SUCCESS' THEN 1 ELSE 0 END) AS failed_tasks,
  ROUND(AVG(TIMESTAMPDIFF(SECOND, start_time, end_time)) / 60.0, 2) AS avg_duration_min,
  ROUND(MAX(TIMESTAMPDIFF(SECOND, start_time, end_time)) / 60.0, 2) AS max_duration_min
FROM oceanbase.DBA_OB_TASK_OPT_STAT_GATHER_HISTORY
WHERE start_time >= DATE_SUB(CURDATE(), INTERVAL {lookback_days} DAY)
  AND end_time IS NOT NULL
GROUP BY DATE(start_time)
ORDER BY task_date DESC
"""
    table_sql = f"""
SELECT
  DATE(task_opt.start_time) AS task_date,
  COUNT(DISTINCT CONCAT(t_opt.owner, '.', t_opt.table_name)) AS total_tables,
  COUNT(DISTINCT CASE WHEN t_opt.status != 'SUCCESS'
        THEN CONCAT(t_opt.owner, '.', t_opt.table_name) END) AS failed_tables
FROM oceanbase.DBA_OB_TASK_OPT_STAT_GATHER_HISTORY task_opt
JOIN oceanbase.DBA_OB_TABLE_OPT_STAT_GATHER_HISTORY t_opt
  ON task_opt.task_id = t_opt.task_id
WHERE task_opt.start_time >= DATE_SUB(CURDATE(), INTERVAL {lookback_days} DAY)
  AND task_opt.end_time IS NOT NULL
  AND t_opt.owner != 'oceanbase'
GROUP BY DATE(task_opt.start_time)
"""
    all_items: list[StatsCollectionDaySummary] = []
    multi_tenant = len(targets) > 1
    for ds in targets:
        ds_cluster = _safe_text(ds.cluster_key, "")
        ds_tenant = _datasource_tenant_name(ds) or ds_cluster
        try:
            task_result = await pool.execute_query(ds, task_sql)
            task_rows = {str(_val(r, "task_date", "")): r for r in _rows_to_dicts(task_result)}
            table_result = await pool.execute_query(ds, table_sql)
            table_rows = {str(_val(r, "task_date", "")): r for r in _rows_to_dicts(table_result)}
        except Exception as exc:
            logger.warning("daily collection summary query failed ds=%s: %s", ds.id, exc)
            continue
        for dt, r in task_rows.items():
            tr = table_rows.get(dt, {})
            total_tables = _int(tr, "total_tables") or 0
            failed_tables = _int(tr, "failed_tables") or 0
            all_items.append(
                StatsCollectionDaySummary(
                    date=dt,
                    task_type=_str(r, "task_types") or "UNKNOWN",
                    total_tasks=_int(r, "total_tasks") or 0,
                    success_tasks=_int(r, "success_tasks") or 0,
                    failed_tasks=_int(r, "failed_tasks") or 0,
                    total_tables=total_tables,
                    success_tables=max(total_tables - failed_tables, 0),
                    failed_tables=failed_tables,
                    avg_duration_min=_float(r, "avg_duration_min") or 0.0,
                    max_duration_min=_float(r, "max_duration_min") or 0.0,
                    cluster_key=ds_cluster if multi_tenant else None,
                    tenant_name=ds_tenant if multi_tenant else None,
                    datasource_id=ds.id,
                )
            )
    all_items.sort(key=lambda x: x.date, reverse=True)
    return StatsCollectionDailySummaryResponse(datasource_id=datasource_id, items=all_items)


@router.get("/daily-failed-tables", response_model=StatsDailyFailedTablesResponse)
async def get_daily_failed_tables(
    datasource_id: int | None = Query(None, ge=1),
    cluster_key: str | None = Query(None),
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: Session = Depends(get_db),
):
    """某天收集失败表列表（按 owner.table_name 去重）。支持多租户聚合。"""
    targets = _resolve_datasources(db, datasource_id, cluster_key)
    if not targets:
        return StatsDailyFailedTablesResponse(datasource_id=datasource_id, date=date, items=[])
    pool = get_db_pool()
    multi_tenant = len(targets) > 1

    sql = f"""
SELECT
  t_opt.owner,
  t_opt.table_name,
  COUNT(*) AS failure_count,
  MAX(t_opt.status) AS latest_status,
  COALESCE(
    NULLIF(MAX(t_opt.stat_refresh_failed_list), ''),
    MAX(t_opt.status)
  ) AS latest_error,
  MAX(TIMESTAMPDIFF(SECOND, t_opt.start_time, t_opt.end_time)) AS latest_gather_seconds,
  MAX(task_opt.start_time) AS latest_task_start_time
FROM (
  SELECT task_id, start_time, end_time
  FROM oceanbase.DBA_OB_TASK_OPT_STAT_GATHER_HISTORY
  WHERE DATE(start_time) = '{date}'
    AND end_time IS NOT NULL
) task_opt
JOIN oceanbase.DBA_OB_TABLE_OPT_STAT_GATHER_HISTORY t_opt
  ON task_opt.task_id = t_opt.task_id
WHERE t_opt.status != 'SUCCESS'
  AND t_opt.owner != 'oceanbase'
GROUP BY t_opt.owner, t_opt.table_name
ORDER BY failure_count DESC, latest_gather_seconds DESC
LIMIT 200
"""
    all_items: list[StatsDailyFailedTableItem] = []
    for ds in targets:
        ds_cluster = _safe_text(ds.cluster_key, "")
        ds_tenant = _datasource_tenant_name(ds) or ds_cluster
        try:
            result = await pool.execute_query(ds, sql)
            rows = _rows_to_dicts(result)
        except Exception as exc:
            logger.warning("daily failed tables query failed ds=%s date=%s: %s", ds.id, date, exc)
            continue
        for r in rows:
            all_items.append(
                StatsDailyFailedTableItem(
                    owner=ds_tenant or _str(r, "owner"),
                    table_name=_str(r, "table_name"),
                    failure_count=_int(r, "failure_count") or 1,
                    latest_status=_str(r, "latest_status"),
                    latest_error=_str(r, "latest_error"),
                    latest_gather_seconds=_int(r, "latest_gather_seconds"),
                    latest_task_start_time=_str(r, "latest_task_start_time"),
                    cluster_key=ds_cluster if multi_tenant else None,
                    tenant_name=ds_tenant if multi_tenant else None,
                    datasource_id=ds.id,
                )
            )
    all_items.sort(key=lambda x: x.failure_count or 0, reverse=True)
    return StatsDailyFailedTablesResponse(datasource_id=datasource_id, date=date, items=all_items)


@router.get("/daily-tasks", response_model=StatsDailyTasksResponse)
async def get_daily_tasks(
    datasource_id: int | None = Query(None, ge=1),
    cluster_key: str | None = Query(None),
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    task_type: str | None = Query(None),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """某天的采集任务列表。支持分页、筛选、多租户聚合。"""
    targets = _resolve_datasources(db, datasource_id, cluster_key)
    if not targets:
        return StatsDailyTasksResponse(
            datasource_id=datasource_id,
            date=date,
            items=[],
            total=0,
            page=page,
            page_size=page_size,
        )
    pool = get_db_pool()
    multi_tenant = len(targets) > 1

    where_extra = ""
    if task_type:
        safe_type = task_type.replace("'", "''")
        where_extra += f"\n  AND type = '{safe_type}'"
    if status:
        safe_status = status.replace("'", "''")
        where_extra += f"\n  AND status = '{safe_status}'"

    sql = f"""
SELECT
  task_id,
  type AS task_type,
  status,
  start_time,
  end_time,
  TIMESTAMPDIFF(SECOND, start_time, end_time) AS duration_seconds,
  table_count,
  failed_count
FROM oceanbase.DBA_OB_TASK_OPT_STAT_GATHER_HISTORY
WHERE DATE(start_time) = '{date}'
  AND end_time IS NOT NULL{where_extra}
ORDER BY start_time ASC
LIMIT 500
"""
    all_items: list[StatsDailyTaskItem] = []
    for ds in targets:
        ds_cluster = _safe_text(ds.cluster_key, "")
        ds_tenant = _datasource_tenant_name(ds) or ds_cluster
        try:
            result = await pool.execute_query(ds, sql)
            rows = _rows_to_dicts(result)
        except Exception as exc:
            logger.warning("daily tasks query failed ds=%s date=%s: %s", ds.id, date, exc)
            continue
        for r in rows:
            all_items.append(
                StatsDailyTaskItem(
                    task_id=_str(r, "task_id"),
                    task_type=_str(r, "task_type"),
                    status=_str(r, "status"),
                    start_time=_str(r, "start_time"),
                    end_time=_str(r, "end_time"),
                    duration_seconds=_int(r, "duration_seconds"),
                    table_count=_int(r, "table_count"),
                    failed_count=_int(r, "failed_count"),
                    cluster_key=ds_cluster if multi_tenant else None,
                    tenant_name=ds_tenant if multi_tenant else None,
                    datasource_id=ds.id,
                )
            )
    all_items.sort(key=lambda x: x.start_time or "")
    total = len(all_items)
    offset = (page - 1) * page_size
    page_items = all_items[offset : offset + page_size]
    return StatsDailyTasksResponse(
        datasource_id=datasource_id,
        date=date,
        items=page_items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/col-stats", response_model=StatsColStatsResponse)
async def get_col_stats(
    datasource_id: int = Query(..., ge=1),
    db_name: str = Query(..., min_length=1),
    table_name: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    """列统计信息（L2 §3.1）"""
    ds = _get_datasource(db, datasource_id)
    pool = get_db_pool()

    sql = f"""
SELECT
  owner,
  table_name,
  column_name,
  num_distinct,
  num_buckets,
  histogram,
  sample_size,
  last_analyzed
FROM oceanbase.DBA_TAB_COL_STATISTICS
WHERE owner = '{db_name}'
  AND table_name = '{table_name}'
ORDER BY column_name
"""
    try:
        result = await pool.execute_query(ds, sql)
        rows = _rows_to_dicts(result)
    except Exception as exc:
        logger.warning("stats col_stats query failed: %s", exc)
        rows = []

    items = [
        StatsColStatItem(
            owner=_str(r, "owner"),
            table_name=_str(r, "table_name"),
            column_name=_str(r, "column_name"),
            num_distinct=_int(r, "num_distinct"),
            num_buckets=_int(r, "num_buckets"),
            histogram=_str(r, "histogram"),
            sample_size=_int(r, "sample_size"),
            last_analyzed=_str(r, "last_analyzed"),
        )
        for r in rows
    ]
    return StatsColStatsResponse(items=items)


@router.get("/histogram", response_model=StatsHistogramResponse)
async def get_histogram(
    datasource_id: int = Query(..., ge=1),
    db_name: str = Query(..., min_length=1),
    table_name: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    """直方图桶分布（L2 §3.2）"""
    ds = _get_datasource(db, datasource_id)
    pool = get_db_pool()

    sql = f"""
SELECT
  owner,
  table_name,
  column_name,
  COUNT(*) AS bucket_cnt,
  MAX(endpoint_repeat_count) AS max_bucket_repeat,
  SUM(endpoint_repeat_count) AS total_repeat,
  ROUND(
    MAX(endpoint_repeat_count) / NULLIF(SUM(endpoint_repeat_count), 0),
    4
  ) AS top_bucket_ratio
FROM oceanbase.DBA_TAB_HISTOGRAMS
WHERE owner = '{db_name}'
  AND table_name = '{table_name}'
GROUP BY owner, table_name, column_name
ORDER BY top_bucket_ratio DESC
"""
    try:
        result = await pool.execute_query(ds, sql)
        rows = _rows_to_dicts(result)
    except Exception as exc:
        logger.warning("stats histogram query failed: %s", exc)
        rows = []

    items = [
        StatsHistogramItem(
            owner=_str(r, "owner"),
            table_name=_str(r, "table_name"),
            column_name=_str(r, "column_name"),
            bucket_cnt=_int(r, "bucket_cnt"),
            max_bucket_repeat=_int(r, "max_bucket_repeat"),
            total_repeat=_int(r, "total_repeat"),
            top_bucket_ratio=_float(r, "top_bucket_ratio"),
        )
        for r in rows
    ]
    return StatsHistogramResponse(items=items)
