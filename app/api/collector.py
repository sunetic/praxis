from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.database import get_db
from app.models import models
from app.services.collector import cleanup, sql_audit, watchlist
from app.services.datasource.router import DataSourceRoutingError, resolve_collector_datasource

router = APIRouter(prefix="/collector", tags=["Collector"])
logger = get_logger("api.collector")

_VALID_MODES = {"all", "threshold", "watchlist", "sample", "cleanup"}


def _get_ds(db: Session, ds_id: int) -> models.DataSource:
    ds = db.query(models.DataSource).filter(models.DataSource.id == ds_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail=f"DataSource {ds_id} not found")
    return ds


@router.post("/run")
async def run_collector(
    source_datasource_id: int,
    mode: str = "all",
    db: Session = Depends(get_db),
):
    if mode not in _VALID_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Valid: {sorted(_VALID_MODES)}")

    source_ds = _get_ds(db, source_datasource_id)
    try:
        target_ds = resolve_collector_datasource(db, "")
    except DataSourceRoutingError as e:
        raise HTTPException(status_code=404, detail=str(e))

    result = {}
    if mode in ("all", "threshold", "watchlist", "sample"):
        result["sql_audit"] = await sql_audit.run_sql_audit_collection(source_ds, target_ds)
    if mode == "cleanup":
        result["cleanup"] = await cleanup.run_cleanup(target_ds)

    return result


@router.get("/status")
async def collector_status(
    source_datasource_id: int,
    db: Session = Depends(get_db),
):
    source_ds = _get_ds(db, source_datasource_id)
    try:
        target_ds = resolve_collector_datasource(db, "")
    except DataSourceRoutingError as e:
        raise HTTPException(status_code=404, detail=str(e))

    from app.db.connection import DBConnectionPool

    pool = DBConnectionPool()
    cp_result = await pool.execute_query(
        target_ds,
        "SELECT source_type, last_value, last_run_at, last_row_count, status, error_msg "
        "FROM collector_checkpoints WHERE source_type LIKE %s",
        params=[f"%_{source_ds.id}"],
    )
    checkpoints = [
        r if isinstance(r, dict) else dict(zip(cp_result["columns"], r)) for r in cp_result["rows"]
    ]
    wl_size = await watchlist.get_size(target_ds, source_ds.id)

    return {
        "source_datasource_id": source_datasource_id,
        "checkpoints": checkpoints,
        "watchlist_size": wl_size,
    }
