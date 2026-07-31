from __future__ import annotations

from typing import Any

from app.services.sql_analysis.history.queries import (
    get_monitor_plan_explain,
    get_monitor_sql_detail,
    get_monitor_sql_trend,
    list_monitor_plan_history,
)

DEFAULT_ROLLUP_SAMPLE_LIMIT = 24


def _to_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sample_rollup_buckets(
    buckets: list[dict[str, Any]],
    sample_limit: int,
) -> tuple[str, list[dict[str, Any]]]:
    if sample_limit < 1:
        raise ValueError("sample_limit must be greater than 0")
    if len(buckets) <= sample_limit:
        return "full", buckets
    if sample_limit == 1:
        return "tail", [buckets[-1]]

    max_index = len(buckets) - 1
    sampled_indexes = sorted(
        {
            min(max_index, round(step * max_index / (sample_limit - 1)))
            for step in range(sample_limit)
        }
    )
    return "evenly_spaced", [buckets[index] for index in sampled_indexes]


def build_sql_rollup_from_trend(
    trend: list[dict[str, Any]],
    *,
    sample_limit: int = DEFAULT_ROLLUP_SAMPLE_LIMIT,
) -> dict[str, Any]:
    normalized_buckets = [
        {
            "bucket_start_us": _to_int(item.get("bucket_start_us")),
            "executions": _to_int(item.get("executions")),
            "avg_elapsed_time_us": _to_float(item.get("avg_elapsed_time_us")),
            "total_elapsed_time_us": _to_int(item.get("total_elapsed_time_us")),
            "avg_cpu_time_us": _to_float(item.get("avg_execute_time_us"))
            if item.get("avg_execute_time_us") is not None
            else None,
        }
        for item in trend
    ]

    sampling_strategy, sampled_buckets = _sample_rollup_buckets(normalized_buckets, sample_limit)
    total_executions = sum(item["executions"] for item in normalized_buckets)
    total_elapsed_time_us = sum(item["total_elapsed_time_us"] for item in normalized_buckets)
    total_cpu_time_us = sum(
        int((item["avg_cpu_time_us"] or 0) * item["executions"])
        for item in normalized_buckets
        if item["avg_cpu_time_us"] is not None
    )
    avg_elapsed_time_us = (
        round(total_elapsed_time_us / total_executions, 2) if total_executions else 0.0
    )
    avg_cpu_time_us = (
        round(total_cpu_time_us / total_executions, 2)
        if total_executions and total_cpu_time_us
        else None
    )

    return {
        "sampling_strategy": sampling_strategy,
        "sample_limit": sample_limit,
        "summary": {
            "source_bucket_count": len(normalized_buckets),
            "sampled_bucket_count": len(sampled_buckets),
            "total_executions": total_executions,
            "total_elapsed_time_us": total_elapsed_time_us,
            "total_cpu_time_us": total_cpu_time_us or None,
            "avg_elapsed_time_us": avg_elapsed_time_us,
            "avg_cpu_time_us": avg_cpu_time_us,
            "max_avg_elapsed_time_us": max(
                (item["avg_elapsed_time_us"] for item in normalized_buckets), default=0.0
            ),
            "latest_avg_elapsed_time_us": normalized_buckets[-1]["avg_elapsed_time_us"]
            if normalized_buckets
            else 0.0,
        },
        "buckets": sampled_buckets,
    }


def build_sql_facts_payload(
    *,
    datasource_id: int,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    detail: dict[str, Any],
    latest_plan: dict[str, Any] | None,
    explain_source: str,
    plan_explain: list[dict[str, Any]],
    plan_history_count: int | None = None,
) -> dict[str, Any]:
    latest_plan = latest_plan or {}
    executions = _to_int(detail.get("executions"))
    avg_elapsed_time_us = _to_float(detail.get("avg_elapsed_time_us"))
    avg_cpu_time_us = (
        _to_float(detail.get("avg_execute_time_us"))
        if detail.get("avg_execute_time_us") is not None
        else None
    )
    total_elapsed_time_us = _to_int(round(executions * avg_elapsed_time_us))
    total_cpu_time_us = (
        _to_int(round(executions * avg_cpu_time_us)) if avg_cpu_time_us is not None else None
    )

    return {
        "datasource_id": datasource_id,
        "sql_id": sql_id,
        "window": {
            "start_time_us": start_time_us,
            "end_time_us": end_time_us,
        },
        "ownership": {
            "datasource_id": datasource_id,
            "ob_tenant_id": detail.get("ob_tenant_id"),
            "tenant_name": detail.get("tenant_name"),
            "db_name": detail.get("db_name"),
            "user_name": detail.get("user_name"),
        },
        "sql_text": detail.get("sql_text"),
        "execution": {
            "executions": executions,
            "avg_elapsed_time_us": avg_elapsed_time_us,
            "max_elapsed_time_us": _to_int(detail.get("max_elapsed_time_us")),
            "latest_request_time_us": detail.get("latest_request_time_us"),
        },
        "resource": {
            "avg_cpu_time_us": avg_cpu_time_us,
            "total_elapsed_time_us": total_elapsed_time_us,
            "total_cpu_time_us": total_cpu_time_us,
        },
        "plan": {
            "plan_count": max(_to_int(detail.get("plan_count")), plan_history_count or 0),
            "latest_plan_id": latest_plan.get("plan_id"),
            "latest_plan_hash": latest_plan.get("plan_hash"),
            "latest_plan_last_active_time": latest_plan.get("last_active_time"),
            "latest_table_scan": latest_plan.get("table_scan"),
            "explain_source": explain_source,
            "explain_item_count": len(plan_explain),
        },
    }


async def build_monitor_sql_facts(
    *,
    datasource_id: int = 0,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    tenant_id: int | None = None,
) -> dict[str, Any] | None:
    detail = await get_monitor_sql_detail(
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        datasource_id=datasource_id,
        tenant_id=tenant_id,
    )
    if detail is None:
        return None

    tenant_id = tenant_id if tenant_id is not None else detail.get("ob_tenant_id")
    latest_plan_history = await list_monitor_plan_history(
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        datasource_id=datasource_id,
        tenant_id=tenant_id,
        limit=1,
    )
    latest_plan = latest_plan_history[0] if latest_plan_history else {}
    explain_source, plan_explain = await get_monitor_plan_explain(
        sql_id=sql_id,
        plan_id=latest_plan.get("plan_id"),
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        datasource_id=datasource_id,
        tenant_id=tenant_id,
    )

    return build_sql_facts_payload(
        datasource_id=datasource_id,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        detail=detail,
        latest_plan=latest_plan,
        explain_source=explain_source,
        plan_explain=plan_explain,
        plan_history_count=len(latest_plan_history),
    )


async def build_monitor_sql_rollup(
    *,
    datasource_id: int = 0,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    tenant_id: int | None = None,
    sample_limit: int = DEFAULT_ROLLUP_SAMPLE_LIMIT,
) -> dict[str, Any]:
    trend = await get_monitor_sql_trend(
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        datasource_id=datasource_id,
        tenant_id=tenant_id,
    )
    rollup = build_sql_rollup_from_trend(trend, sample_limit=sample_limit)
    return {
        "datasource_id": datasource_id,
        "sql_id": sql_id,
        "window": {
            "start_time_us": start_time_us,
            "end_time_us": end_time_us,
        },
        **rollup,
    }
