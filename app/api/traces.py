"""Trace query API — browse spans collected by the SQLite exporter."""

import json
import sqlite3
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core.config import get_settings
from app.tracing.cleanup import purge_expired_spans

router = APIRouter(prefix="/traces", tags=["traces"])
settings = get_settings()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.tracing_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ns_to_ms(ns: int) -> float:
    return round(ns / 1_000_000, 2)


def _build_span_tree(rows: list[dict]) -> list[dict]:
    """Build a nested span tree from flat rows."""
    by_id: dict[str, dict] = {}
    roots: list[dict] = []
    for row in rows:
        node = {
            "span_id": row["span_id"],
            "name": row["name"],
            "kind": row["kind"],
            "duration_ms": _ns_to_ms(row["end_time_ns"] - row["start_time_ns"]),
            "status": row["status"],
            "attributes": json.loads(row["attributes"]) if row["attributes"] else {},
            "children": [],
        }
        by_id[row["span_id"]] = node

    for row in rows:
        node = by_id[row["span_id"]]
        parent_id = row["parent_span_id"]
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(node)
        else:
            roots.append(node)

    return roots


@router.get("")
def list_traces(
    minutes: int = Query(default=60, ge=1, le=1440),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """List recent traces, grouped by trace_id."""
    if not settings.tracing_enabled:
        return []
    cutoff_ns = int((time.time() - minutes * 60) * 1e9)
    try:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT trace_id,
                   MIN(start_time_ns) AS first_start,
                   MAX(end_time_ns) AS last_end,
                   COUNT(*) AS span_count,
                   MIN(name) AS root_name
            FROM spans
            WHERE start_time_ns >= ?
            GROUP BY trace_id
            ORDER BY first_start DESC
            LIMIT ?
            """,
            (cutoff_ns, limit),
        ).fetchall()
        conn.close()
    except Exception:
        return []

    return [
        {
            "trace_id": r["trace_id"],
            "root_span": r["root_name"],
            "duration_ms": _ns_to_ms(r["last_end"] - r["first_start"]),
            "span_count": r["span_count"],
            "started_at_ns": r["first_start"],
        }
        for r in rows
    ]


@router.get("/slow")
def list_slow_traces(
    threshold_ms: float = Query(default=1000, ge=0),
    minutes: int = Query(default=60, ge=1, le=1440),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """List traces exceeding a duration threshold."""
    if not settings.tracing_enabled:
        return []
    cutoff_ns = int((time.time() - minutes * 60) * 1e9)
    threshold_ns = int(threshold_ms * 1_000_000)
    try:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT trace_id,
                   MIN(start_time_ns) AS first_start,
                   MAX(end_time_ns) AS last_end,
                   COUNT(*) AS span_count,
                   MIN(name) AS root_name
            FROM spans
            WHERE start_time_ns >= ?
            GROUP BY trace_id
            HAVING (MAX(end_time_ns) - MIN(start_time_ns)) >= ?
            ORDER BY (MAX(end_time_ns) - MIN(start_time_ns)) DESC
            LIMIT ?
            """,
            (cutoff_ns, threshold_ns, limit),
        ).fetchall()
        conn.close()
    except Exception:
        return []

    return [
        {
            "trace_id": r["trace_id"],
            "root_span": r["root_name"],
            "duration_ms": _ns_to_ms(r["last_end"] - r["first_start"]),
            "span_count": r["span_count"],
            "started_at_ns": r["first_start"],
        }
        for r in rows
    ]


@router.get("/{trace_id}")
def get_trace(trace_id: str) -> dict[str, Any]:
    """Get full span tree for a single trace."""
    if not settings.tracing_enabled:
        raise HTTPException(status_code=404, detail="Tracing disabled")
    try:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT span_id, parent_span_id, name, kind,
                   start_time_ns, end_time_ns, status, attributes
            FROM spans
            WHERE trace_id = ?
            ORDER BY start_time_ns ASC
            """,
            (trace_id,),
        ).fetchall()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not rows:
        raise HTTPException(status_code=404, detail="Trace not found")

    row_dicts = [dict(r) for r in rows]
    tree = _build_span_tree(row_dicts)
    first_start = min(r["start_time_ns"] for r in row_dicts)
    last_end = max(r["end_time_ns"] for r in row_dicts)

    return {
        "trace_id": trace_id,
        "duration_ms": _ns_to_ms(last_end - first_start),
        "span_count": len(row_dicts),
        "spans": tree,
    }


@router.post("/cleanup")
def cleanup_traces() -> dict[str, Any]:
    """Manually trigger cleanup of expired spans."""
    deleted = purge_expired_spans(settings.tracing_db_path, settings.tracing_retention_hours)
    return {"deleted": deleted, "retention_hours": settings.tracing_retention_hours}
