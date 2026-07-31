import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models import models
from app.schemas import schemas

try:
    from app.services.datasource.probe import probe_and_fill_ob_ids as _probe_and_fill_ob_ids
except ImportError:
    _probe_and_fill_ob_ids = None

router = APIRouter(prefix="/datasources", tags=["DataSources"])
logger = get_logger("api.datasources")


def _normalize_datasource_payload(payload: dict) -> dict:
    if not payload.get("cluster_key"):
        host = payload.get("host") or ""
        port = payload.get("port") or 0
        payload["cluster_key"] = f"{host}:{port}"
    return payload


def _ensure_single_sys_per_cluster(
    db: Session,
    cluster_key: str,
    tenant_role: str,
    exclude_id: int | None = None,
) -> None:
    if tenant_role != "sys":
        return

    query = db.query(models.DataSource).filter(
        models.DataSource.cluster_key == cluster_key,
        models.DataSource.tenant_role == "sys",
    )
    if exclude_id is not None:
        query = query.filter(models.DataSource.id != exclude_id)
    existing = query.first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Cluster '{cluster_key}' already has a sys datasource (id={existing.id}).",
        )


@router.post("/test")
async def test_connection(datasource: schemas.DataSourceCreate):
    """Test database connection"""
    from app.db.connection import get_db_pool

    logger.info(
        "test_connection_start %s",
        fmt_kv(
            host=datasource.host,
            port=datasource.port,
            tenant_role=datasource.tenant_role,
            cluster_key=datasource.cluster_key,
        ),
    )
    pool = get_db_pool()

    try:
        success, message = await asyncio.wait_for(
            pool.test_connection(
                datasource.host,
                datasource.port,
                datasource.user,
                datasource.password,
                datasource.database,
                db_type=datasource.db_type,
            ),
            timeout=15,
        )
    except TimeoutError:
        logger.warning("test_connection_timeout %s", fmt_kv(host=datasource.host))
        return {"success": False, "message": "Connection timed out (15s)"}

    if not success:
        logger.warning("test_connection_failed %s", fmt_kv(host=datasource.host))
        return {"success": False, "message": f"Connection failed: {message}"}

    logger.info("test_connection_success %s", fmt_kv(host=datasource.host))
    return {"success": True, "message": "Connection successful"}


@router.post("/{datasource_id}/test")
async def test_saved_connection(datasource_id: int, db: Session = Depends(get_db)):
    """Test a saved datasource connection (backend uses stored credentials)"""
    from app.db.connection import get_db_pool

    datasource = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not datasource:
        logger.warning("test_saved_connection_not_found %s", fmt_kv(datasource_id=datasource_id))
        raise HTTPException(status_code=404, detail="DataSource not found")

    logger.info("test_saved_connection_start %s", fmt_kv(datasource_id=datasource_id))

    pool = get_db_pool()

    try:
        success, message = await asyncio.wait_for(
            pool.test_connection(
                datasource.host,
                datasource.port,
                datasource.user or "",
                datasource.password or "",
                datasource.database or "",
                db_type=datasource.db_type or "mysql",
            ),
            timeout=15,
        )
    except TimeoutError:
        logger.warning("test_saved_connection_timeout %s", fmt_kv(datasource_id=datasource_id))
        return {"success": False, "message": "Connection timed out (15s)"}
    if not success:
        logger.warning("test_saved_connection_failed %s", fmt_kv(datasource_id=datasource_id))
        return {"success": False, "message": f"Connection failed: {message}"}

    logger.info("test_saved_connection_success %s", fmt_kv(datasource_id=datasource_id))
    return {"success": True, "message": "Connection successful"}


@router.get("", response_model=list[schemas.DataSourceResponse])
def list_datasources(db: Session = Depends(get_db)):
    records = db.query(models.DataSource).all()
    logger.info("list_datasources %s", fmt_kv(count=len(records)))
    return records


@router.get("/{datasource_id}", response_model=schemas.DataSourceResponse)
def get_datasource(datasource_id: int, db: Session = Depends(get_db)):
    datasource = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not datasource:
        logger.warning("get_datasource_not_found %s", fmt_kv(datasource_id=datasource_id))
        raise HTTPException(status_code=404, detail="DataSource not found")
    logger.info("get_datasource %s", fmt_kv(datasource_id=datasource_id))
    return datasource


@router.get("/{datasource_id}/connect-info", response_model=schemas.DataSourceConnectInfo)
def get_datasource_connect_info(datasource_id: int, db: Session = Depends(get_db)):
    datasource = db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    if not datasource:
        raise HTTPException(status_code=404, detail="DataSource not found")
    return datasource


@router.post("", response_model=schemas.DataSourceResponse, status_code=status.HTTP_201_CREATED)
async def create_datasource(datasource: schemas.DataSourceCreate, db: Session = Depends(get_db)):
    from app.db.connection import get_db_pool

    payload = _normalize_datasource_payload(datasource.model_dump())
    _ensure_single_sys_per_cluster(
        db,
        cluster_key=payload["cluster_key"],
        tenant_role=payload["tenant_role"],
    )
    db_datasource = models.DataSource(**payload)
    db.add(db_datasource)
    db.commit()
    db.refresh(db_datasource)
    logger.info(
        "create_datasource %s",
        fmt_kv(
            datasource_id=db_datasource.id,
            host=db_datasource.host,
            tenant_role=db_datasource.tenant_role,
            cluster_key=db_datasource.cluster_key,
        ),
    )
    if _probe_and_fill_ob_ids is not None:
        await _probe_and_fill_ob_ids(db_datasource, get_db_pool())
    db.commit()
    db.refresh(db_datasource)
    return db_datasource


@router.patch("/{datasource_id}", response_model=schemas.DataSourceResponse)
async def update_datasource(
    datasource_id: int,
    datasource_update: schemas.DataSourceUpdate,
    db: Session = Depends(get_db),
):
    from app.db.connection import get_db_pool

    db_datasource = (
        db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    )
    if not db_datasource:
        logger.warning("update_datasource_not_found %s", fmt_kv(datasource_id=datasource_id))
        raise HTTPException(status_code=404, detail="DataSource not found")

    update_data = datasource_update.model_dump(exclude_unset=True)
    current_payload = {
        "name": db_datasource.name,
        "host": db_datasource.host,
        "port": db_datasource.port,
        "db_type": db_datasource.db_type,
        "cluster_key": db_datasource.cluster_key,
        "tenant_role": db_datasource.tenant_role,
        "user": db_datasource.user,
        "password": db_datasource.password,
        "database": db_datasource.database,
        "status": db_datasource.status,
    }
    update_data = _normalize_datasource_payload({**current_payload, **update_data})
    _ensure_single_sys_per_cluster(
        db,
        cluster_key=update_data["cluster_key"],
        tenant_role=update_data["tenant_role"],
        exclude_id=datasource_id,
    )
    for field, value in update_data.items():
        if hasattr(db_datasource, field):
            setattr(db_datasource, field, value)

    db.commit()
    db.refresh(db_datasource)
    logger.info(
        "update_datasource %s",
        fmt_kv(
            datasource_id=datasource_id,
            tenant_role=db_datasource.tenant_role,
            cluster_key=db_datasource.cluster_key,
        ),
    )
    if _probe_and_fill_ob_ids is not None:
        await _probe_and_fill_ob_ids(db_datasource, get_db_pool())
    db.commit()
    db.refresh(db_datasource)
    return db_datasource


@router.delete("/{datasource_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_datasource(datasource_id: int, db: Session = Depends(get_db)):
    db_datasource = (
        db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
    )
    if not db_datasource:
        logger.warning("delete_datasource_not_found %s", fmt_kv(datasource_id=datasource_id))
        raise HTTPException(status_code=404, detail="DataSource not found")

    db.delete(db_datasource)
    db.commit()
    logger.info("delete_datasource %s", fmt_kv(datasource_id=datasource_id))
    return None
