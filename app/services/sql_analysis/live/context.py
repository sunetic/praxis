from __future__ import annotations

import json
from typing import Any

from app.models import models
from app.services.llm import get_llm_client
from app.services.sql_analysis.live.queries import (
    get_live_plan_explain,
    get_live_sql_detail,
    list_live_plan_history,
)
from app.services.sql_analysis.live.signals import build_live_signals
from app.services.sql_analysis.utils import (
    _normalize_json_value,
    _parse_llm_json_object,
    _truncate_text,
)

_UNAVAILABLE_DIMENSIONS = [
    {
        "key": "executions",
        "label": "Execution Count",
        "reason": "Current stage only reads live database statistics; no historical sampling layer is available.",
    },
    {
        "key": "trend",
        "label": "Historical Trend",
        "reason": "Current stage does not retain time-series aggregated data.",
    },
    {
        "key": "regression",
        "label": "Regression Detection",
        "reason": "No historical baseline window is available for regression comparison.",
    },
    {
        "key": "plan_history",
        "label": "Plan Evolution History",
        "reason": "Only realtime plan cache facts are shown; this does not represent the full historical evolution.",
    },
]


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _build_live_llm_context(context: dict[str, Any]) -> dict[str, Any]:
    facts = dict(context.get("facts") or {})
    if facts.get("sql_text") is not None:
        facts["sql_text"] = _truncate_text(facts.get("sql_text"), 4000)
    facts["objects"] = list((facts.get("objects") or [])[:12])
    facts["current_plans"] = list((facts.get("current_plans") or [])[:5])
    llm_context = {
        "database_type": context.get("database_type"),
        "datasource_id": context.get("datasource_id"),
        "sql_id": context.get("sql_id"),
        "start_time_us": context.get("start_time_us"),
        "end_time_us": context.get("end_time_us"),
        "facts": facts,
        "signals": context.get("signals") or [],
        "plan_explain": {
            "source": ((context.get("plan_explain") or {}).get("source")),
            "items": list(((context.get("plan_explain") or {}).get("items") or [])[:30]),
        },
        "plan_details": [
            {
                "plan_id": item.get("plan_id"),
                "plan_hash": item.get("plan_hash"),
                "last_active_time": item.get("last_active_time"),
                "table_scan": item.get("table_scan"),
                "explain_source": item.get("explain_source"),
                "objects": list((item.get("objects") or [])[:12]),
                "explain_items": list((item.get("explain_items") or [])[:20]),
            }
            for item in (context.get("plan_details") or [])[:5]
        ],
    }
    return _normalize_json_value(llm_context)


async def build_live_sql_context(
    datasource: models.DataSource,
    *,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    tenant_id: int | None = None,
) -> dict[str, Any]:
    detail = await get_live_sql_detail(
        datasource,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        tenant_id=tenant_id,
    )
    if detail is None:
        raise ValueError("SQL detail not found")

    current_plans = await list_live_plan_history(
        datasource,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        tenant_id=tenant_id,
        limit=5,
    )
    latest_plan = current_plans[0] if current_plans else None
    explain_source = "unavailable"
    plan_explain: list[dict[str, Any]] = []
    if latest_plan is not None:
        explain_source, plan_explain = await get_live_plan_explain(
            datasource,
            sql_id=sql_id,
            plan_id=latest_plan.get("plan_id"),
            plan_hash=latest_plan.get("plan_hash"),
            tenant_id=latest_plan.get("tenant_id")
            if latest_plan.get("tenant_id") is not None
            else tenant_id,
            sql_text=detail.get("sql_text"),
        )
    objects = _unique_strings([str(item.get("object_name") or "") for item in plan_explain])
    plan_details: list[dict[str, Any]] = []
    for index, current_plan in enumerate(current_plans):
        is_current = index == 0
        plan_details.append(
            {
                "plan_id": current_plan.get("plan_id"),
                "plan_hash": current_plan.get("plan_hash"),
                "last_active_time": current_plan.get("last_active_time"),
                "table_scan": current_plan.get("table_scan"),
                "explain_source": explain_source if is_current else "unavailable",
                "objects": objects if is_current else [],
                "explain_items": plan_explain if is_current else [],
            }
        )
    signals = build_live_signals(
        sql_text=detail.get("sql_text"),
        current_plans=current_plans,
        plan_explain=plan_explain,
        explain_source=explain_source,
    )

    facts = {
        "datasource_id": datasource.id,
        "sql_id": sql_id,
        "start_time_us": start_time_us,
        "end_time_us": end_time_us,
        "cluster_key": datasource.cluster_key,
        "tenant_id": tenant_id if tenant_id is not None else detail.get("tenant_id"),
        "db_name": detail.get("db_name"),
        "user_name": detail.get("user_name"),
        "sql_text": detail.get("sql_text"),
        "latest_request_time_us": detail.get("latest_request_time_us"),
        "current_plan": {
            "plan_id": latest_plan.get("plan_id") if latest_plan else None,
            "plan_hash": latest_plan.get("plan_hash") if latest_plan else None,
            "last_active_time": latest_plan.get("last_active_time") if latest_plan else None,
            "table_scan": latest_plan.get("table_scan") if latest_plan else None,
            "explain_source": explain_source,
            "explain_item_count": len(plan_explain),
        },
        "current_plans": current_plans,
        "window_plan_total": len(current_plans),
        "current_plan_id": latest_plan.get("plan_id") if latest_plan else None,
        "objects": objects,
        "unavailable_dimensions": list(_UNAVAILABLE_DIMENSIONS),
    }

    return {
        "database_type": datasource.db_type,
        "datasource_id": datasource.id,
        "sql_id": sql_id,
        "start_time_us": start_time_us,
        "end_time_us": end_time_us,
        "facts": facts,
        "signals": signals,
        "current_plans": current_plans,
        "window_plan_total": len(current_plans),
        "current_plan_id": latest_plan.get("plan_id") if latest_plan else None,
        "plan_explain": {
            "source": explain_source,
            "items": plan_explain,
        },
        "plan_details": plan_details,
    }


async def explain_live_sql_with_ai(
    datasource: models.DataSource,
    *,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    tenant_id: int | None = None,
) -> dict[str, Any]:
    context = await build_live_sql_context(
        datasource,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        tenant_id=tenant_id,
    )
    system_prompt = (
        "You are a database SQL live-diagnostics assistant.\n"
        "Use the database_type field to interpret database-specific facts and plan output.\n"
        "You only explain facts based on the given realtime context; do not fabricate historical trends, execution counts, or regression conclusions.\n"
        "Return JSON with fixed object fields: summary, risk_points, investigation_steps, optimization_directions.\n"
        "Requirements:\n"
        "1) summary: 2-4 sentences.\n"
        "2) risk_points / investigation_steps / optimization_directions are string arrays.\n"
        "3) Must reflect the boundary that this is a realtime perspective with insufficient historical evidence.\n"
        "4) Do not output markdown or extra fields."
    )
    user_prompt = (
        "Based on the following realtime SQL analysis context, produce a diagnostic explanation:\n"
        + json.dumps(
            _build_live_llm_context(context),
            ensure_ascii=False,
        )
    )
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
        raise ValueError("LLM returned no response payload")
    content = (
        ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    ).strip()
    payload = _parse_llm_json_object(content)
    return {
        "datasource_id": datasource.id,
        "sql_id": sql_id,
        "context": context,
        "summary": str(payload.get("summary") or "").strip(),
        "risk_points": [
            str(item).strip() for item in (payload.get("risk_points") or []) if str(item).strip()
        ],
        "investigation_steps": [
            str(item).strip()
            for item in (payload.get("investigation_steps") or [])
            if str(item).strip()
        ],
        "optimization_directions": [
            str(item).strip()
            for item in (payload.get("optimization_directions") or [])
            if str(item).strip()
        ],
    }
