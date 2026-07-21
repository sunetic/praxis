from __future__ import annotations

import json
import time
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.logging import fmt_kv, get_logger
from app.db.connection import get_db_pool
from app.db.database import get_db
from app.models import models
from app.schemas import schemas
from app.services.llm import get_llm_client

router = APIRouter(prefix="/session-analysis", tags=["SessionAnalysis"])
logger = get_logger("app.api.sessions")

_LONG_TRANS_SECONDS = 60
_TXN_SQL_SAMPLE_LIMIT = 5


def _resolve_execution_datasource(db: Session, datasource_id: int) -> models.DataSource:
    ds = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="DataSource not found")
    if ds.tenant_role != "sys":
        mapped = (
            db.query(models.DataSource)
            .filter(
                models.DataSource.cluster_key == ds.cluster_key,
                models.DataSource.tenant_role == "sys",
            )
            .order_by(models.DataSource.id.asc())
            .first()
        )
        if not mapped:
            raise HTTPException(status_code=400, detail="No executable session-analysis datasource in current cluster")
        return mapped
    return ds


def _tenant_id_from_datasource(ds: models.DataSource) -> int | None:
    attrs = ds.attributes if isinstance(ds.attributes, dict) else {}
    for key in ("ob_tenant_id", "tenant_id"):
        raw = attrs.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def _query_role(ds: models.DataSource) -> str:
    return "sys" if str(ds.tenant_role or "").lower() == "sys" else "user"


def _row_tenant_id(row: dict[str, Any]) -> int | None:
    raw = _row_value(row, "TENANT_ID", "tenant_id")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _matches_tenant_scope(
    *,
    row_tenant_id: int | None,
    scope_tenant_id: int | None,
) -> bool:
    if scope_tenant_id is None:
        return True
    return row_tenant_id is not None and row_tenant_id == scope_tenant_id


def _active_datasources_query(db: Session):
    return (
        db.query(models.DataSource)
        .filter(models.DataSource.status == "active")
        .order_by(models.DataSource.id.asc())
    )


def _select_execution_datasources(
    scope_datasources: list[models.DataSource],
    *,
    scope_tenant_id: int | None,
) -> list[models.DataSource]:
    sys_datasources = [item for item in scope_datasources if str(item.tenant_role or "").lower() == "sys"]
    if sys_datasources:
        return sys_datasources

    user_datasources = [item for item in scope_datasources if str(item.tenant_role or "").lower() != "sys"]
    if scope_tenant_id is None:
        return user_datasources

    scoped = [ds for ds in user_datasources if _tenant_id_from_datasource(ds) == scope_tenant_id]
    return scoped if scoped else user_datasources


def _resolve_scope_datasources(
    db: Session,
    datasource_id: int | None,
    cluster_key: str | None,
    *,
    scope_tenant_id: int | None,
) -> tuple[models.DataSource | None, list[models.DataSource], list[models.DataSource]]:
    selected = None
    if datasource_id is not None:
        selected = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
        if not selected:
            raise HTTPException(status_code=404, detail="DataSource not found")

    normalized_cluster_key = str(cluster_key or "").strip() or None
    if selected is not None:
        scope_cluster_key = selected.cluster_key
    else:
        scope_cluster_key = normalized_cluster_key

    if scope_cluster_key is not None:
        scope_datasources = _active_datasources_query(db).filter(models.DataSource.cluster_key == scope_cluster_key).all()
        if selected is not None and not scope_datasources:
            scope_datasources = [selected]
    else:
        scope_datasources = _active_datasources_query(db).all()

    execution_datasources = _select_execution_datasources(
        scope_datasources,
        scope_tenant_id=scope_tenant_id,
    )
    return selected, scope_datasources, execution_datasources


def _coerce_active_time_us(value: object) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, datetime):
        return int(value.timestamp() * 1_000_000)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _fmt_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def _row_value(row: dict, *keys: str) -> object:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _normalize_sql_list(rows: Iterable[dict]) -> dict[int, list[str]]:
    by_session: dict[int, list[str]] = {}
    seen_by_session: dict[int, set[str]] = {}
    for row in rows:
        session_raw = _row_value(row, "SESSION_ID", "session_id")
        sql_raw = _row_value(row, "QUERY_SQL", "query_sql", "CURRENT_SQL", "current_sql")
        if session_raw is None or sql_raw is None:
            continue
        try:
            session_id = int(session_raw)
        except (TypeError, ValueError):
            continue
        sql_text = str(sql_raw).strip()
        if not sql_text:
            continue
        session_seen = seen_by_session.setdefault(session_id, set())
        if sql_text in session_seen:
            continue
        session_seen.add(sql_text)
        by_session.setdefault(session_id, []).append(sql_text[:500])
        if len(by_session[session_id]) >= _TXN_SQL_SAMPLE_LIMIT:
            continue
    return by_session


async def _fetch_transaction_sql_samples(ds: models.DataSource, session_ids: list[int]) -> dict[int, list[str]]:
    unique_session_ids = sorted({session_id for session_id in session_ids if session_id > 0})
    if not unique_session_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(unique_session_ids))
    sql = f"""
        SELECT
            SESSION_ID,
            QUERY_SQL,
            REQUEST_TIME
        FROM oceanbase.GV$OB_SQL_AUDIT
        WHERE SESSION_ID IN ({placeholders})
          AND QUERY_SQL IS NOT NULL
          AND TRIM(QUERY_SQL) <> ''
        ORDER BY REQUEST_TIME DESC
        LIMIT 1000
    """
    result = await get_db_pool().execute_query(ds, sql, role=_query_role(ds), params=unique_session_ids)
    return _normalize_sql_list(result.get("rows", []))


@router.get("/live/sessions", response_model=schemas.LiveSessionListResponse)
async def list_live_sessions(
    datasource_id: int | None = Query(None, ge=1),
    cluster_key: str | None = Query(None, min_length=1),
    tenant_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
) -> schemas.LiveSessionListResponse:
    if datasource_id is None and cluster_key is None:
        active_datasource = db.query(models.DataSource.id).filter(models.DataSource.status == "active").first()
        if active_datasource is None:
            return schemas.LiveSessionListResponse(
                datasource_id=None,
                total=0,
                active=0,
                sessions=[],
            )
    _selected_ds, _cluster_datasources, execution_datasources = _resolve_scope_datasources(
        db,
        datasource_id,
        cluster_key,
        scope_tenant_id=tenant_id,
    )
    if not execution_datasources:
        return schemas.LiveSessionListResponse(
            datasource_id=datasource_id,
            total=0,
            active=0,
            sessions=[],
        )

    sql = """
        SELECT
            ID          AS session_id,
            USER        AS user,
            HOST        AS client_ip,
            DB          AS db,
            COMMAND     AS command,
            TIME        AS time_seconds,
            STATE       AS state,
            INFO        AS current_sql,
            TENANT      AS tenant_name
        FROM oceanbase.GV$OB_PROCESSLIST
        ORDER BY TIME DESC
        LIMIT 500
    """
    sessions: list[schemas.LiveSession] = []
    dedupe_keys: set[tuple[int | None, int, str, str]] = set()
    query_errors: list[str] = []
    for ds in execution_datasources:
        try:
            result = await get_db_pool().execute_query(ds, sql, role=_query_role(ds))
        except Exception as exc:
            query_errors.append(str(exc))
            continue

        ds_tenant_id = _tenant_id_from_datasource(ds)
        if not _matches_tenant_scope(row_tenant_id=ds_tenant_id, scope_tenant_id=tenant_id):
            continue
        for row in result.get("rows", []):
            command = str(row.get("command") or "")
            host_raw = str(row.get("client_ip") or "")
            user = str(row.get("user") or "")
            session_id = int(row.get("session_id") or 0)
            dedupe_key = (ds_tenant_id, session_id, user, str(row.get("db") or ""))
            if dedupe_key in dedupe_keys:
                continue
            dedupe_keys.add(dedupe_key)
            tenant_name_value = str(row.get("tenant_name") or "").strip() or None
            identity_label = f"{user}@{tenant_name_value}" if user and tenant_name_value else user
            sessions.append(
                schemas.LiveSession(
                    datasource_id=ds.id,
                    session_id=session_id,
                    user=user,
                    identity_label=identity_label,
                    tenant_name=tenant_name_value,
                    client_ip=host_raw.split(":")[0] if host_raw else None,
                    db=str(row.get("db") or "") or None,
                    command=command,
                    time_seconds=int(float(row.get("time_seconds") or 0)),
                    state="ACTIVE" if command.upper() not in ("SLEEP", "") else "SLEEP",
                    current_sql=str(row.get("current_sql") or "")[:500] or None,
                    ob_tenant_id=ds_tenant_id,
                )
            )

    if not sessions and query_errors:
        logger.warning(
            "list_live_sessions_failed %s errors=%s",
            fmt_kv(datasource_id=datasource_id, cluster_key=cluster_key, tenant_id=tenant_id),
            "; ".join(query_errors),
        )
        raise HTTPException(status_code=502, detail=f"Failed to query sessions: {query_errors[0]}")

    sessions.sort(key=lambda item: item.time_seconds, reverse=True)

    active = sum(1 for s in sessions if s.state == "ACTIVE")
    return schemas.LiveSessionListResponse(
        datasource_id=datasource_id,
        total=len(sessions),
        active=active,
        sessions=sessions,
    )


@router.get("/live/transactions", response_model=schemas.LiveTransactionListResponse)
async def list_live_transactions(
    datasource_id: int | None = Query(None, ge=1),
    cluster_key: str | None = Query(None, min_length=1),
    tenant_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
) -> schemas.LiveTransactionListResponse:
    if datasource_id is None and cluster_key is None:
        active_datasource = db.query(models.DataSource.id).filter(models.DataSource.status == "active").first()
        if active_datasource is None:
            return schemas.LiveTransactionListResponse(
                datasource_id=None,
                long_transactions=[],
                pending_transactions=[],
            )
    _selected_ds, _cluster_datasources, execution_datasources = _resolve_scope_datasources(
        db,
        datasource_id,
        cluster_key,
        scope_tenant_id=tenant_id,
    )
    if not execution_datasources:
        return schemas.LiveTransactionListResponse(
            datasource_id=datasource_id,
            long_transactions=[],
            pending_transactions=[],
        )

    sql = """
        SELECT
            TX_ID,
            SESSION_ID,
            TENANT_ID,
            STATE,
            ACTIVE_TIME,
            PARTICIPANTS,
            COORDINATOR
        FROM oceanbase.GV$OB_TRANSACTION_SCHEDULERS
        ORDER BY ACTIVE_TIME ASC
        LIMIT 200
    """
    now_us = int(time.time() * 1_000_000)

    long_txns: list[schemas.LiveTransaction] = []
    pending_txns: list[schemas.LiveTransaction] = []
    dedupe_keys: set[tuple[str, int | None, str]] = set()
    query_errors: list[str] = []
    sql_samples_by_source: dict[tuple[int, int], list[str]] = {}
    for ds in execution_datasources:
        try:
            result = await get_db_pool().execute_query(ds, sql, role=_query_role(ds))
        except Exception as exc:
            query_errors.append(str(exc))
            continue
        session_ids: list[int] = []
        fallback_tenant_id = _tenant_id_from_datasource(ds)
        per_source_txns: list[schemas.LiveTransaction] = []
        for row in result.get("rows", []):
            row_tenant_id = _row_tenant_id(row)
            resolved_tenant_id = row_tenant_id if row_tenant_id is not None else fallback_tenant_id
            if not _matches_tenant_scope(row_tenant_id=resolved_tenant_id, scope_tenant_id=tenant_id):
                continue
            active_time_us = _coerce_active_time_us(_row_value(row, "ACTIVE_TIME", "active_time"))
            elapsed_seconds = max(0, (now_us - active_time_us) // 1_000_000) if active_time_us else 0
            state_raw = str(_row_value(row, "STATE", "state") or "").upper()
            is_pending = state_raw not in ("ACTIVE", "")
            participants_raw = str(_row_value(row, "PARTICIPANTS", "participants") or "")
            participant_count = len(participants_raw.split(",")) if participants_raw else 1
            coordinator = str(_row_value(row, "COORDINATOR", "coordinator") or "")
            is_distributed = "," in participants_raw or coordinator not in ("", "NULL", "None")
            session_id = _row_value(row, "SESSION_ID", "session_id")
            normalized_session_id = int(session_id) if session_id not in (None, "") else None
            resolved_tenant_id = row_tenant_id if row_tenant_id is not None else fallback_tenant_id
            dedupe_key = (str(_row_value(row, "TX_ID", "tx_id") or ""), resolved_tenant_id, str(normalized_session_id or ""))
            if dedupe_key in dedupe_keys:
                continue
            dedupe_keys.add(dedupe_key)
            txn = schemas.LiveTransaction(
                datasource_id=ds.id,
                trans_hash=str(_row_value(row, "TX_ID", "tx_id") or ""),
                session_id=normalized_session_id,
                tenant_id=resolved_tenant_id,
                trans_type="DISTRIBUTED" if is_distributed else "LOCAL",
                state="PENDING_COMMIT" if is_pending else "ACTIVE",
                elapsed_seconds=elapsed_seconds,
                participants=participant_count,
                sql_list=[],
            )
            if is_pending:
                pending_txns.append(txn)
                per_source_txns.append(txn)
                if normalized_session_id:
                    session_ids.append(normalized_session_id)
            elif elapsed_seconds >= _LONG_TRANS_SECONDS:
                long_txns.append(txn)
                per_source_txns.append(txn)
                if normalized_session_id:
                    session_ids.append(normalized_session_id)

        if session_ids:
            try:
                by_session = await _fetch_transaction_sql_samples(ds, session_ids)
                for sid, sql_list in by_session.items():
                    sql_samples_by_source[(ds.id, sid)] = sql_list
            except Exception as exc:
                logger.warning(
                    "list_live_transactions_sql_samples_failed %s error=%s",
                    fmt_kv(datasource_id=datasource_id, cluster_key=cluster_key, source_datasource_id=ds.id),
                    str(exc),
                )
        for txn in per_source_txns:
            if txn.session_id:
                txn.sql_list = sql_samples_by_source.get((ds.id, txn.session_id), [])

    if not long_txns and not pending_txns and query_errors:
        logger.warning(
            "list_live_transactions_failed %s errors=%s",
            fmt_kv(datasource_id=datasource_id, cluster_key=cluster_key, tenant_id=tenant_id),
            "; ".join(query_errors),
        )
        raise HTTPException(status_code=502, detail=f"Failed to query transactions: {query_errors[0]}")

    long_txns.sort(key=lambda item: item.elapsed_seconds, reverse=True)
    pending_txns.sort(key=lambda item: item.elapsed_seconds, reverse=True)

    return schemas.LiveTransactionListResponse(
        datasource_id=datasource_id,
        long_transactions=long_txns,
        pending_transactions=pending_txns,
    )


@router.post("/live/sessions/{session_id}/kill", response_model=schemas.SessionKillResponse)
async def kill_session(
    session_id: int,
    datasource_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> schemas.SessionKillResponse:
    ds = _resolve_execution_datasource(db, datasource_id)

    try:
        await get_db_pool().execute_query(ds, f"KILL CONNECTION {session_id}", role="sys")
        logger.info("session_killed %s", fmt_kv(datasource_id=datasource_id, session_id=session_id))
        return schemas.SessionKillResponse(session_id=session_id, killed=True, message="Session killed")
    except Exception as exc:
        err = str(exc)
        # Session may have already ended — treat as success
        if "Unknown thread id" in err or "not exist" in err.lower():
            return schemas.SessionKillResponse(session_id=session_id, killed=True, message="Session already ended")
        logger.warning("kill_session_failed %s error=%s", fmt_kv(session_id=session_id), err)
        raise HTTPException(status_code=502, detail=f"Kill failed: {err}") from exc


@router.post("/live/analyze")
async def analyze_session_snapshot(
    snapshot: schemas.SessionSnapshotForAI,
) -> StreamingResponse:
    """
    Stream AI analysis for the current session/transaction snapshot.
    Accepts a pre-built snapshot dict; streams SSE text/event-stream.
    """

    async def _generate():
        prompt = _build_analysis_prompt(snapshot)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一位 OceanBase 数据库专家，擅长分析连接状态与事务健康。"
                    "用简洁中文回答，直接给出判断和建议，不要废话。"
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            async for chunk in get_llm_client().chat(messages, stream=True):
                delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if delta:
                    yield f"data: {json.dumps({'type': 'text', 'data': delta}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'data': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


def _build_analysis_prompt(snapshot: schemas.SessionSnapshotForAI) -> str:
    lines: list[str] = [
        f"当前集群快照：总会话 {snapshot.total}，活跃 {snapshot.active}，"
        f"长事务 {snapshot.long_transaction_count} 个，未提交事务 {snapshot.pending_transaction_count} 个。",
    ]

    if snapshot.user_distribution:
        top_users = sorted(snapshot.user_distribution.items(), key=lambda x: -x[1])[:5]
        lines.append("用户分布：" + "、".join(f"{u}({n})" for u, n in top_users))

    if snapshot.ip_distribution:
        top_ips = sorted(snapshot.ip_distribution.items(), key=lambda x: -x[1])[:5]
        lines.append("来源 IP：" + "、".join(f"{ip}({n})" for ip, n in top_ips))

    if snapshot.long_transactions:
        lines.append("长事务摘要：")
        for txn in snapshot.long_transactions[:5]:
            elapsed = _fmt_seconds(txn.get("elapsed_seconds", 0))
            sql_preview = "；".join((txn.get("sql_list") or [])[:3])
            lines.append(f"  - 类型={txn.get('trans_type')}，已运行 {elapsed}，SQL：{sql_preview or '(无)'}")

    if snapshot.pending_transaction_count > 0:
        lines.append(f"有 {snapshot.pending_transaction_count} 个事务卡在 commit 阶段，请重点关注是否存在锁等待。")

    lines.append("\n请给出：1）一句话整体健康判断；2）最需要关注的问题（如有）；3）建议操作（如有）。")
    return "\n".join(lines)
