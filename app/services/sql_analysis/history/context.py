from __future__ import annotations

import json
from typing import Any

from app.schemas import schemas
from app.services.llm import get_llm_client
from app.services.sql_analysis.history.facts_builder import (
    build_monitor_sql_rollup,
    build_sql_facts_payload,
)
from app.services.sql_analysis.history.queries import (
    get_monitor_plan_explain,
    get_monitor_sql_detail,
    get_monitor_sql_trend,
    list_monitor_plan_history,
)
from app.services.sql_analysis.history.signals import (
    build_monitor_signals,
    detect_monitor_matched_categories,
)
from app.services.sql_analysis.utils import (
    _normalize_json_value,
    _parse_llm_json_object,
    _truncate_text,
)


def _build_llm_context(context: dict[str, Any]) -> dict[str, Any]:
    facts = dict(context.get("facts") or {})
    rollup = dict(context.get("rollup") or {})
    summary = dict(rollup.get("summary") or {})
    buckets = list(rollup.get("buckets") or [])[:24]
    if facts.get("sql_text") is not None:
        facts["sql_text"] = _truncate_text(facts.get("sql_text"), 4000)

    llm_context = {
        "datasource_id": context.get("datasource_id"),
        "sql_id": context.get("sql_id"),
        "category": context.get("category"),
        "start_time_us": context.get("start_time_us"),
        "end_time_us": context.get("end_time_us"),
        "matched_categories": context.get("matched_categories") or [],
        "signals": context.get("signals") or [],
        "facts": facts or None,
        "rollup": {
            "sampling_strategy": rollup.get("sampling_strategy"),
            "sample_limit": rollup.get("sample_limit"),
            "summary": summary,
            "buckets": buckets,
        }
        if rollup
        else None,
    }
    return _normalize_json_value(llm_context)


async def build_monitor_sql_context(
    *,
    datasource_id: int,
    category: schemas.SqlMonitorCategory,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    tenant_id: int | None = None,
) -> dict[str, Any]:
    detail = await get_monitor_sql_detail(
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        datasource_id=datasource_id,
        tenant_id=tenant_id,
    )
    tenant_id = (
        tenant_id if tenant_id is not None else (detail.get("ob_tenant_id") if detail else None)
    )
    trend = await get_monitor_sql_trend(
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        datasource_id=datasource_id,
        tenant_id=tenant_id,
    )
    plan_history = await list_monitor_plan_history(
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        datasource_id=datasource_id,
        tenant_id=tenant_id,
        limit=10,
    )
    explain_source, plan_explain = await get_monitor_plan_explain(
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        plan_id=plan_history[0].get("plan_id") if plan_history else None,
        datasource_id=datasource_id,
        tenant_id=tenant_id,
    )

    facts = (
        build_sql_facts_payload(
            datasource_id=datasource_id,
            sql_id=sql_id,
            start_time_us=start_time_us,
            end_time_us=end_time_us,
            detail=detail,
            latest_plan=plan_history[0] if plan_history else None,
            explain_source=explain_source,
            plan_explain=plan_explain,
            plan_history_count=len(plan_history),
        )
        if detail
        else None
    )
    rollup = await build_monitor_sql_rollup(
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        datasource_id=datasource_id,
        tenant_id=tenant_id,
    )
    matched_categories = await detect_monitor_matched_categories(
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        datasource_id=datasource_id,
    )

    signals = build_monitor_signals(
        category=category,
        matched_categories=matched_categories,
        facts=facts,
    )

    return {
        "datasource_id": datasource_id,
        "sql_id": sql_id,
        "category": category,
        "start_time_us": start_time_us,
        "end_time_us": end_time_us,
        "matched_categories": matched_categories,
        "signals": signals,
        "facts": facts,
        "rollup": rollup,
        "detail": detail,
        "trend": trend,
        "plan_history": plan_history,
        "plan_explain": {
            "source": explain_source,
            "items": plan_explain,
        },
    }


async def explain_monitor_sql_with_ai(
    *,
    datasource_id: int,
    category: schemas.SqlMonitorCategory,
    sql_id: str,
    start_time_us: int,
    end_time_us: int,
    tenant_id: int | None = None,
) -> dict[str, Any]:
    context = await build_monitor_sql_context(
        datasource_id=datasource_id,
        category=category,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        tenant_id=tenant_id,
    )
    system_prompt = (
        "你是 OceanBase SQL 诊断助手。\n"
        "你只基于给定 context 解释事实，不要编造未提供的数据。\n"
        "返回 JSON，对象字段固定为：summary, risk_points, investigation_steps, optimization_directions。\n"
        "要求：\n"
        "1) summary 用中文 2-4 句。\n"
        "2) risk_points / investigation_steps / optimization_directions 都是字符串数组。\n"
        "3) 不要输出 markdown，不要输出额外字段。\n"
        "4) 如果证据不足，明确写出“监控证据不足”，不要假装结论确定。"
    )
    user_prompt = "请基于以下 SQL analysis context 输出诊断解释：\n" + json.dumps(
        _build_llm_context(context),
        ensure_ascii=False,
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
        "datasource_id": datasource_id,
        "sql_id": sql_id,
        "category": category,
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
