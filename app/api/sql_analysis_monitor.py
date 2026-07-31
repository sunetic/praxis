"""Monitor (historical) SQL analysis endpoints — requires collector DB."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.core.logging import fmt_kv, get_logger
from app.schemas import schemas
from app.services.sql_analysis.history.context import (
    build_monitor_sql_context,
    explain_monitor_sql_with_ai,
)
from app.services.sql_analysis.history.facts_builder import (
    DEFAULT_ROLLUP_SAMPLE_LIMIT,
    build_monitor_sql_facts,
    build_monitor_sql_rollup,
)
from app.services.sql_analysis.history.queries import (
    MonitorCategoryQuery,
    derive_compare_window,
    get_monitor_plan_explain,
    get_monitor_sql_detail,
    get_monitor_sql_trend,
    list_monitor_category,
    list_monitor_plan_history,
)

router = APIRouter(prefix="/sql-analysis", tags=["SQLAnalysis"])
logger = get_logger("app.api.sql_analysis_monitor")

_DETAIL_RESPONSE_EXCLUDED_KEYS = {"datasource_id", "sql_id", "start_time_us", "end_time_us"}


def _detail_response_payload(payload: dict | None) -> dict:
    return {k: v for k, v in (payload or {}).items() if k not in _DETAIL_RESPONSE_EXCLUDED_KEYS}


@router.get(
    "/monitor/categories/{category}",
    response_model=schemas.SqlMonitorCategoryResponse,
)
async def list_monitor_sql_category(
    category: schemas.SqlMonitorCategory,
    datasource_id: int | None = Query(None, ge=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    limit: int = Query(50, ge=1, le=200),
    compare_start_time_us: int | None = Query(None, ge=0),
    compare_end_time_us: int | None = Query(None, ge=0),
    ob_tenant_id: int | None = Query(None, ge=0),
    db_name: str | None = None,
    sql_id: str | None = None,
    keyword: str | None = None,
    slow_threshold_us: int = Query(1_000_000, ge=1),
    cursor: str | None = Query(None),
):
    query = MonitorCategoryQuery(
        category=category,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        limit=limit,
        compare_start_time_us=compare_start_time_us,
        compare_end_time_us=compare_end_time_us,
        datasource_id=datasource_id,
        db_name=db_name,
        sql_id=sql_id,
        keyword=keyword,
        slow_threshold_us=slow_threshold_us,
        cursor=cursor,
    )
    try:
        items, next_cursor, has_more = await list_monitor_category(query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    resolved_compare_start_time_us, resolved_compare_end_time_us = derive_compare_window(query)

    logger.info(
        "list_monitor_sql_category %s",
        fmt_kv(
            category=category.value,
            datasource_id=datasource_id,
            items=len(items),
            has_more=has_more,
            start_time_us=start_time_us,
            end_time_us=end_time_us,
        ),
    )
    return schemas.SqlMonitorCategoryResponse(
        category=category,
        datasource_id=datasource_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        compare_start_time_us=resolved_compare_start_time_us,
        compare_end_time_us=resolved_compare_end_time_us,
        limit=limit,
        items=[schemas.SqlMonitorCategoryItem.model_validate(item) for item in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/monitor/sql-detail", response_model=schemas.SqlDetailResponse)
async def get_monitor_sql_detail_api(
    datasource_id: int = Query(..., ge=1),
    sql_id: str = Query(..., min_length=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    ob_tenant_id: int | None = Query(None, ge=0),
):
    payload = await get_monitor_sql_detail(
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        datasource_id=datasource_id,
        tenant_id=ob_tenant_id,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="SQL detail not found")
    payload = {k: v for k, v in payload.items() if k not in {"sql_id", "datasource_id"}}
    return schemas.SqlDetailResponse(
        datasource_id=datasource_id,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        **payload,
    )


@router.get("/monitor/sql-trend", response_model=list[schemas.SqlTrendPoint])
async def get_monitor_sql_trend_api(
    datasource_id: int = Query(..., ge=1),
    sql_id: str = Query(..., min_length=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    ob_tenant_id: int | None = Query(None, ge=0),
):
    rows = await get_monitor_sql_trend(
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        datasource_id=datasource_id,
        tenant_id=ob_tenant_id,
    )
    return [schemas.SqlTrendPoint.model_validate(item) for item in rows]


@router.get("/monitor/plan-history", response_model=list[schemas.SqlPlanHistoryItem])
async def list_monitor_plan_history_api(
    datasource_id: int = Query(..., ge=1),
    sql_id: str = Query(..., min_length=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    ob_tenant_id: int | None = Query(None, ge=0),
    limit: int = Query(20, ge=1, le=200),
):
    rows = await list_monitor_plan_history(
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        datasource_id=datasource_id,
        tenant_id=ob_tenant_id,
        limit=limit,
    )
    return [schemas.SqlPlanHistoryItem.model_validate(item) for item in rows]


@router.get("/monitor/plan-explain", response_model=schemas.SqlPlanExplainResponse)
async def get_monitor_plan_explain_api(
    datasource_id: int = Query(..., ge=1),
    sql_id: str = Query(..., min_length=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    plan_id: int | None = Query(None, ge=1),
    ob_tenant_id: int | None = Query(None, ge=0),
):
    source, rows = await get_monitor_plan_explain(
        sql_id=sql_id,
        plan_id=plan_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        datasource_id=datasource_id,
        tenant_id=ob_tenant_id,
    )
    return schemas.SqlPlanExplainResponse(
        datasource_id=datasource_id,
        sql_id=sql_id,
        plan_id=plan_id,
        source=source,
        items=[schemas.SqlPlanExplainItem.model_validate(item) for item in rows],
    )


@router.get("/monitor/sql-facts", response_model=schemas.SqlFactsResponse)
async def get_monitor_sql_facts_api(
    datasource_id: int = Query(..., ge=1),
    sql_id: str = Query(..., min_length=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    ob_tenant_id: int | None = Query(None, ge=0),
):
    payload = await build_monitor_sql_facts(
        datasource_id=datasource_id,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        tenant_id=ob_tenant_id,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="SQL facts not found")
    return schemas.SqlFactsResponse.model_validate(payload)


@router.get("/monitor/sql-rollup", response_model=schemas.SqlRollupResponse)
async def get_monitor_sql_rollup_api(
    datasource_id: int = Query(..., ge=1),
    sql_id: str = Query(..., min_length=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    ob_tenant_id: int | None = Query(None, ge=0),
    sample_limit: int = Query(DEFAULT_ROLLUP_SAMPLE_LIMIT, ge=1, le=240),
):
    payload = await build_monitor_sql_rollup(
        datasource_id=datasource_id,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        tenant_id=ob_tenant_id,
        sample_limit=sample_limit,
    )
    return schemas.SqlRollupResponse.model_validate(payload)


@router.get("/monitor/build-context", response_model=schemas.SqlAnalysisContextResponse)
async def build_monitor_sql_context_api(
    datasource_id: int = Query(..., ge=1),
    category: schemas.SqlMonitorCategory = Query(...),
    sql_id: str = Query(..., min_length=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    ob_tenant_id: int | None = Query(None, ge=0),
):
    payload = await build_monitor_sql_context(
        datasource_id=datasource_id,
        category=category,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        tenant_id=ob_tenant_id,
    )
    return schemas.SqlAnalysisContextResponse(
        datasource_id=datasource_id,
        sql_id=sql_id,
        category=category,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        ob_tenant_id=payload.get("facts", {}).get("ownership", {}).get("ob_tenant_id")
        if payload.get("facts")
        else None,
        matched_categories=payload["matched_categories"],
        signals=[schemas.SqlAnalysisSignal.model_validate(item) for item in payload["signals"]],
        facts=schemas.SqlFactsResponse.model_validate(payload["facts"])
        if payload.get("facts")
        else None,
        rollup=schemas.SqlRollupResponse.model_validate(payload["rollup"])
        if payload.get("rollup")
        else None,
        detail=schemas.SqlDetailResponse(
            datasource_id=datasource_id,
            sql_id=sql_id,
            start_time_us=start_time_us,
            end_time_us=end_time_us,
            **_detail_response_payload(payload.get("detail")),
        )
        if payload.get("detail")
        else None,
        trend=[schemas.SqlTrendPoint.model_validate(item) for item in payload["trend"]],
        plan_history=[
            schemas.SqlPlanHistoryItem.model_validate(item) for item in payload["plan_history"]
        ],
        plan_explain=schemas.SqlPlanExplainResponse(
            datasource_id=datasource_id,
            sql_id=sql_id,
            plan_id=(
                payload["plan_history"][0].get("plan_id") if payload["plan_history"] else None
            ),
            source=payload["plan_explain"]["source"],
            items=[
                schemas.SqlPlanExplainItem.model_validate(item)
                for item in payload["plan_explain"]["items"]
            ],
        ),
    )


@router.post("/monitor/explain-with-ai", response_model=schemas.SqlAnalysisAiExplainResponse)
async def explain_monitor_sql_with_ai_api(
    datasource_id: int = Query(..., ge=1),
    category: schemas.SqlMonitorCategory = Query(...),
    sql_id: str = Query(..., min_length=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    ob_tenant_id: int | None = Query(None, ge=0),
):
    try:
        payload = await explain_monitor_sql_with_ai(
            datasource_id=datasource_id,
            category=category,
            sql_id=sql_id,
            start_time_us=start_time_us,
            end_time_us=end_time_us,
            tenant_id=ob_tenant_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI explanation failed: {exc}") from exc
    context = payload["context"]
    return schemas.SqlAnalysisAiExplainResponse(
        datasource_id=datasource_id,
        sql_id=sql_id,
        category=category,
        context=schemas.SqlAnalysisContextResponse(
            datasource_id=datasource_id,
            sql_id=sql_id,
            category=category,
            start_time_us=start_time_us,
            end_time_us=end_time_us,
            ob_tenant_id=context.get("facts", {}).get("ownership", {}).get("ob_tenant_id")
            if context.get("facts")
            else None,
            matched_categories=context["matched_categories"],
            signals=[schemas.SqlAnalysisSignal.model_validate(item) for item in context["signals"]],
            facts=schemas.SqlFactsResponse.model_validate(context["facts"])
            if context.get("facts")
            else None,
            rollup=schemas.SqlRollupResponse.model_validate(context["rollup"])
            if context.get("rollup")
            else None,
            detail=schemas.SqlDetailResponse(
                datasource_id=datasource_id,
                sql_id=sql_id,
                start_time_us=start_time_us,
                end_time_us=end_time_us,
                **_detail_response_payload(context.get("detail")),
            )
            if context.get("detail")
            else None,
            trend=[schemas.SqlTrendPoint.model_validate(item) for item in context["trend"]],
            plan_history=[
                schemas.SqlPlanHistoryItem.model_validate(item) for item in context["plan_history"]
            ],
            plan_explain=schemas.SqlPlanExplainResponse(
                datasource_id=datasource_id,
                sql_id=sql_id,
                plan_id=(
                    context["plan_history"][0].get("plan_id") if context["plan_history"] else None
                ),
                source=context["plan_explain"]["source"],
                items=[
                    schemas.SqlPlanExplainItem.model_validate(item)
                    for item in context["plan_explain"]["items"]
                ],
            ),
        ),
        summary=payload["summary"],
        risk_points=payload["risk_points"],
        investigation_steps=payload["investigation_steps"],
        optimization_directions=payload["optimization_directions"],
    )
