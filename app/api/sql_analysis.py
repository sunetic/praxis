from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models import models
from app.schemas import schemas
from app.services.datasource.router import (
    DataSourceRoutingError,
    resolve_preferred_execution_datasource,
)
from app.services.sql_analysis.live.context import (
    build_live_sql_context,
    explain_live_sql_with_ai,
)
from app.services.sql_analysis.live.queries import (
    LiveCategoryQuery,
    LiveDbNamesQuery,
    LiveSqlProfileQuery,
    get_live_plan_explain,
    get_live_sql_detail,
    get_live_sql_trend,
    list_live_category,
    list_live_db_names,
    list_live_plan_history,
    list_live_sql_profiles,
)
from app.services.sql_analysis.live.queries import (
    derive_compare_window as derive_live_compare_window,
)

router = APIRouter(prefix="/sql-analysis", tags=["SQLAnalysis"])
logger = get_logger("app.api.sql_analysis")


def _enrich_live_discovery_item(
    db: Session,
    source_datasource: models.DataSource,
    item: dict,
) -> dict:
    enriched = dict(item)
    enriched["source_datasource_id"] = source_datasource.id
    try:
        routed = resolve_preferred_execution_datasource(
            db,
            source_datasource.id,
            tenant_id=item.get("tenant_id"),
            db_name=item.get("db_name"),
        )
        enriched["preferred_execution_datasource_id"] = routed.datasource.id
    except DataSourceRoutingError:
        enriched["preferred_execution_datasource_id"] = source_datasource.id
    return enriched


# ── Live endpoints ──────────────────────────────────────────────────────────


@router.get(
    "/live/discovery",
    response_model=schemas.SqlLiveDiscoveryResponse,
)
async def list_live_sql_discovery(
    datasource_id: int = Query(..., ge=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    limit: int = Query(20, ge=1, le=200),
    tenant_id: int | None = Query(None, ge=0),
    tenant_name: str | None = None,
    db_name: str | None = None,
    sql_id: str | None = None,
    keyword: str | None = None,
    db: Session = Depends(get_db),
):
    datasource = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not datasource:
        raise HTTPException(status_code=404, detail="DataSource not found")

    resolved_tenant_id = tenant_id
    if resolved_tenant_id is None and datasource.attributes:
        ob_tenant_id = datasource.attributes.get("ob_tenant_id")
        if isinstance(ob_tenant_id, int):
            resolved_tenant_id = ob_tenant_id
    query = LiveSqlProfileQuery(
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        limit=limit,
        tenant_id=resolved_tenant_id,
        tenant_name=tenant_name,
        db_name=db_name,
        sql_id=sql_id,
        keyword=keyword,
    )
    try:
        items = await list_live_sql_profiles(datasource, query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return schemas.SqlLiveDiscoveryResponse(
        datasource_id=datasource_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        limit=limit,
        items=[
            schemas.SqlLiveDiscoveryItem.model_validate(
                _enrich_live_discovery_item(db, datasource, item)
            )
            for item in items
        ],
    )


@router.get("/live/db-names", response_model=schemas.SqlLiveDbNamesResponse)
async def list_live_db_names_api(
    datasource_id: int = Query(..., ge=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    tenant_id: int | None = Query(None, ge=0),
    tenant_name: str | None = None,
    db: Session = Depends(get_db),
):
    datasource = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not datasource:
        raise HTTPException(status_code=404, detail="DataSource not found")

    resolved_tenant_id = tenant_id
    if resolved_tenant_id is None and datasource.attributes:
        ob_tenant_id = datasource.attributes.get("ob_tenant_id")
        if isinstance(ob_tenant_id, int):
            resolved_tenant_id = ob_tenant_id
    query = LiveDbNamesQuery(
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        tenant_id=resolved_tenant_id,
        tenant_name=tenant_name,
    )
    try:
        items = await list_live_db_names(datasource, query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return schemas.SqlLiveDbNamesResponse(
        datasource_id=datasource_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        items=items,
    )


@router.get(
    "/live/categories/{category}",
    response_model=schemas.SqlMonitorCategoryResponse,
)
async def list_live_sql_category(
    category: schemas.SqlMonitorCategory,
    datasource_id: int = Query(..., ge=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    limit: int = Query(20, ge=1, le=200),
    compare_start_time_us: int | None = Query(None, ge=0),
    compare_end_time_us: int | None = Query(None, ge=0),
    tenant_id: int | None = Query(None, ge=0),
    tenant_name: str | None = None,
    db_name: str | None = None,
    sql_id: str | None = None,
    keyword: str | None = None,
    slow_threshold_us: int = Query(1_000_000, ge=1),
    db: Session = Depends(get_db),
):
    datasource = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not datasource:
        raise HTTPException(status_code=404, detail="DataSource not found")

    query = LiveCategoryQuery(
        category=category,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        limit=limit,
        compare_start_time_us=compare_start_time_us,
        compare_end_time_us=compare_end_time_us,
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        db_name=db_name,
        sql_id=sql_id,
        keyword=keyword,
        slow_threshold_us=slow_threshold_us,
    )
    try:
        items = await list_live_category(datasource, query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    resolved_compare_start_time_us, resolved_compare_end_time_us = derive_live_compare_window(query)
    logger.info(
        "list_live_sql_category %s",
        fmt_kv(category=category.value, datasource_id=datasource_id, items=len(items)),
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
    )


@router.get("/live/sql-detail", response_model=schemas.SqlDetailResponse)
async def get_live_sql_detail_api(
    datasource_id: int = Query(..., ge=1),
    sql_id: str = Query(..., min_length=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    tenant_id: int | None = Query(None, ge=0),
    db: Session = Depends(get_db),
):
    datasource = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not datasource:
        raise HTTPException(status_code=404, detail="DataSource not found")

    payload = await get_live_sql_detail(
        datasource,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        tenant_id=tenant_id,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="SQL detail not found")
    payload = {k: v for k, v in payload.items() if k not in {"sql_id"}}
    return schemas.SqlDetailResponse(
        datasource_id=datasource_id,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        **payload,
    )


@router.get("/live/sql-trend", response_model=list[schemas.SqlTrendPoint])
async def get_live_sql_trend_api(
    datasource_id: int = Query(..., ge=1),
    sql_id: str = Query(..., min_length=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    interval_seconds: int = Query(60, ge=1, le=3600),
    tenant_id: int | None = Query(None, ge=0),
    db: Session = Depends(get_db),
):
    datasource = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not datasource:
        raise HTTPException(status_code=404, detail="DataSource not found")

    rows = await get_live_sql_trend(
        datasource,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        interval_seconds=interval_seconds,
        tenant_id=tenant_id,
    )
    return [schemas.SqlTrendPoint.model_validate(item) for item in rows]


@router.get("/live/plan-history", response_model=list[schemas.SqlPlanHistoryItem])
async def list_live_plan_history_api(
    datasource_id: int = Query(..., ge=1),
    sql_id: str = Query(..., min_length=1),
    tenant_id: int | None = Query(None, ge=0),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    datasource = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not datasource:
        raise HTTPException(status_code=404, detail="DataSource not found")

    rows = await list_live_plan_history(datasource, sql_id=sql_id, tenant_id=tenant_id, limit=limit)
    return [schemas.SqlPlanHistoryItem.model_validate(item) for item in rows]


@router.post("/live/plan-explain", response_model=schemas.SqlPlanExplainResponse)
async def get_live_plan_explain_api(
    datasource_id: int = Query(..., ge=1),
    sql_id: str = Query(..., min_length=1),
    plan_id: int | None = Query(None, ge=1),
    plan_hash: int | None = Query(None, ge=1),
    tenant_id: int | None = Query(None, ge=0),
    sql_text: str | None = Body(None, embed=True),
    db_name: str | None = Body(None, embed=True),
    db: Session = Depends(get_db),
):
    datasource = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not datasource:
        raise HTTPException(status_code=404, detail="DataSource not found")

    source, rows = await get_live_plan_explain(
        datasource,
        sql_id=sql_id,
        plan_id=plan_id,
        plan_hash=plan_hash,
        tenant_id=tenant_id,
        sql_text=sql_text,
        db_name=db_name,
    )
    return schemas.SqlPlanExplainResponse(
        datasource_id=datasource_id,
        sql_id=sql_id,
        plan_id=plan_id,
        source=source,
        items=[schemas.SqlPlanExplainItem.model_validate(item) for item in rows],
    )


@router.get("/live/build-context", response_model=schemas.SqlLiveAnalysisContextResponse)
async def build_live_sql_context_api(
    datasource_id: int = Query(..., ge=1),
    sql_id: str = Query(..., min_length=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    tenant_id: int | None = Query(None, ge=0),
    db: Session = Depends(get_db),
):
    datasource = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not datasource:
        raise HTTPException(status_code=404, detail="DataSource not found")

    try:
        payload = await build_live_sql_context(
            datasource,
            sql_id=sql_id,
            start_time_us=start_time_us,
            end_time_us=end_time_us,
            tenant_id=tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return schemas.SqlLiveAnalysisContextResponse(
        datasource_id=datasource_id,
        sql_id=sql_id,
        start_time_us=start_time_us,
        end_time_us=end_time_us,
        facts=schemas.SqlLiveFactsResponse.model_validate(payload["facts"]),
        signals=[schemas.SqlAnalysisSignal.model_validate(item) for item in payload["signals"]],
        current_plans=[
            schemas.SqlPlanHistoryItem.model_validate(item) for item in payload["current_plans"]
        ],
        window_plan_total=int(payload.get("window_plan_total") or 0),
        current_plan_id=payload.get("current_plan_id"),
        plan_explain=schemas.SqlPlanExplainResponse(
            datasource_id=datasource_id,
            sql_id=sql_id,
            plan_id=(
                payload["current_plans"][0].get("plan_id") if payload["current_plans"] else None
            ),
            source=payload["plan_explain"]["source"],
            items=[
                schemas.SqlPlanExplainItem.model_validate(item)
                for item in payload["plan_explain"]["items"]
            ],
        ),
        plan_details=[
            schemas.SqlLivePlanDetailResponse(
                plan_id=item.get("plan_id"),
                plan_hash=item.get("plan_hash"),
                last_active_time=item.get("last_active_time"),
                table_scan=item.get("table_scan"),
                explain_source=str(item.get("explain_source") or "unavailable"),
                objects=[str(obj) for obj in (item.get("objects") or [])],
                explain_items=[
                    schemas.SqlPlanExplainItem.model_validate(explain)
                    for explain in item.get("explain_items") or []
                ],
            )
            for item in payload.get("plan_details") or []
        ],
    )


@router.post("/live/explain-with-ai", response_model=schemas.SqlLiveAnalysisAiExplainResponse)
async def explain_live_sql_with_ai_api(
    datasource_id: int = Query(..., ge=1),
    sql_id: str = Query(..., min_length=1),
    start_time_us: int = Query(..., ge=0),
    end_time_us: int = Query(..., ge=0),
    tenant_id: int | None = Query(None, ge=0),
    db: Session = Depends(get_db),
):
    datasource = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not datasource:
        raise HTTPException(status_code=404, detail="DataSource not found")

    try:
        payload = await explain_live_sql_with_ai(
            datasource,
            sql_id=sql_id,
            start_time_us=start_time_us,
            end_time_us=end_time_us,
            tenant_id=tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI explanation failed: {exc}") from exc
    context = payload["context"]
    return schemas.SqlLiveAnalysisAiExplainResponse(
        datasource_id=datasource_id,
        sql_id=sql_id,
        context=schemas.SqlLiveAnalysisContextResponse(
            datasource_id=datasource_id,
            sql_id=sql_id,
            start_time_us=start_time_us,
            end_time_us=end_time_us,
            facts=schemas.SqlLiveFactsResponse.model_validate(context["facts"]),
            signals=[schemas.SqlAnalysisSignal.model_validate(item) for item in context["signals"]],
            current_plans=[
                schemas.SqlPlanHistoryItem.model_validate(item) for item in context["current_plans"]
            ],
            window_plan_total=int(context.get("window_plan_total") or 0),
            current_plan_id=context.get("current_plan_id"),
            plan_explain=schemas.SqlPlanExplainResponse(
                datasource_id=datasource_id,
                sql_id=sql_id,
                plan_id=(
                    context["current_plans"][0].get("plan_id") if context["current_plans"] else None
                ),
                source=context["plan_explain"]["source"],
                items=[
                    schemas.SqlPlanExplainItem.model_validate(item)
                    for item in context["plan_explain"]["items"]
                ],
            ),
            plan_details=[
                schemas.SqlLivePlanDetailResponse(
                    plan_id=item.get("plan_id"),
                    plan_hash=item.get("plan_hash"),
                    last_active_time=item.get("last_active_time"),
                    table_scan=item.get("table_scan"),
                    explain_source=str(item.get("explain_source") or "unavailable"),
                    objects=[str(obj) for obj in (item.get("objects") or [])],
                    explain_items=[
                        schemas.SqlPlanExplainItem.model_validate(explain)
                        for explain in item.get("explain_items") or []
                    ],
                )
                for item in context.get("plan_details") or []
            ],
        ),
        summary=payload["summary"],
        risk_points=payload["risk_points"],
        investigation_steps=payload["investigation_steps"],
        optimization_directions=payload["optimization_directions"],
    )
