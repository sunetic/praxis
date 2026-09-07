from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, selectinload

from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models import models
from app.schemas import schemas
from app.services.integration.http_service import (
    ServiceHTTPError,
    call_http_service,
    config_for_storage,
    public_service_config,
)

router = APIRouter(prefix="/services", tags=["Services"])
logger = get_logger("api.services")


def _query_services(db: Session):
    return db.query(models.Service).options(selectinload(models.Service.knowledge_bases))


def _serialize_service(service: models.Service) -> dict[str, Any]:
    return {
        "id": service.id,
        "name": service.name,
        "service_type": service.service_type,
        "config": public_service_config(service.config),
        "resource_ref": service.resource_ref,
        "status": service.status,
        "has_credentials": service.has_credentials,
        "knowledge_base_ids": service.knowledge_base_ids,
        "created_at": service.created_at,
        "updated_at": service.updated_at,
    }


def _resolve_knowledge_bases(db: Session, ids: list[int]) -> list[models.KnowledgeBase]:
    unique_ids = list(dict.fromkeys(ids))
    if not unique_ids:
        return []
    records = db.query(models.KnowledgeBase).filter(models.KnowledgeBase.id.in_(unique_ids)).all()
    found = {item.id for item in records}
    missing = [item for item in unique_ids if item not in found]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Knowledge bases not found: {', '.join(map(str, missing))}",
        )
    by_id = {item.id: item for item in records}
    return [by_id[item] for item in unique_ids]


def _secret_payload(payload: schemas.ServiceSecretConfig | None) -> dict[str, Any] | None:
    if payload is None or not payload.has_values():
        return None
    return payload.model_dump()


@router.get("", response_model=list[schemas.ServiceResponse])
def list_services(db: Session = Depends(get_db)):
    records = _query_services(db).order_by(models.Service.id.asc()).all()
    logger.info("list_services %s", fmt_kv(count=len(records)))
    return [_serialize_service(item) for item in records]


@router.get("/{service_id}", response_model=schemas.ServiceResponse)
def get_service(service_id: int, db: Session = Depends(get_db)):
    service = _query_services(db).filter(models.Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    return _serialize_service(service)


@router.post("", response_model=schemas.ServiceResponse, status_code=status.HTTP_201_CREATED)
def create_service(payload: schemas.ServiceCreate, db: Session = Depends(get_db)):
    db_service = models.Service(
        name=payload.name,
        service_type=payload.service_type,
        config=config_for_storage(payload.config),
        secrets=_secret_payload(payload.secrets),
        resource_ref=payload.resource_ref,
        knowledge_bases=_resolve_knowledge_bases(db, payload.knowledge_base_ids),
    )
    db.add(db_service)
    db.commit()
    db.refresh(db_service)
    logger.info(
        "create_service %s",
        fmt_kv(
            service_id=db_service.id,
            name=db_service.name,
            service_type=db_service.service_type,
            knowledge_base_count=len(db_service.knowledge_bases),
        ),
    )
    return _serialize_service(db_service)


@router.patch("/{service_id}", response_model=schemas.ServiceResponse)
def update_service(
    service_id: int,
    payload: schemas.ServiceUpdate,
    db: Session = Depends(get_db),
):
    db_service = _query_services(db).filter(models.Service.id == service_id).first()
    if not db_service:
        raise HTTPException(status_code=404, detail="Service not found")

    update = payload.model_dump(exclude_unset=True, exclude={"secrets", "knowledge_base_ids"})
    if "config" in update and update["config"] is not None:
        update["config"] = config_for_storage(payload.config)
    for field, value in update.items():
        setattr(db_service, field, value)

    if "secrets" in payload.model_fields_set and payload.secrets is not None:
        supplied = payload.secrets.model_dump()
        merged = dict(db_service.secrets or {})
        for key, value in supplied.items():
            if value:
                merged[key] = value
        db_service.secrets = merged or None
    elif payload.config is not None and payload.config.auth_type == "none":
        db_service.secrets = None
    if "knowledge_base_ids" in payload.model_fields_set:
        db_service.knowledge_bases = _resolve_knowledge_bases(db, payload.knowledge_base_ids or [])

    db.commit()
    db.refresh(db_service)
    logger.info("update_service %s", fmt_kv(service_id=service_id))
    return _serialize_service(db_service)


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(service_id: int, db: Session = Depends(get_db)):
    db_service = _query_services(db).filter(models.Service.id == service_id).first()
    if not db_service:
        raise HTTPException(status_code=404, detail="Service not found")
    db_service.knowledge_bases = []
    db.delete(db_service)
    db.commit()
    logger.info("delete_service %s", fmt_kv(service_id=service_id))
    return None


async def _test_connection(
    *,
    service_id: int | None,
    service_type: str,
    config: dict[str, Any],
    secrets: dict[str, Any] | None,
) -> schemas.ServiceTestResponse:
    path = str(config.get("health_check_path") or "/")
    method = str(config.get("health_check_method") or "GET")
    try:
        result = await call_http_service(
            service_id=service_id,
            service_type=service_type,
            raw_config=config,
            raw_secrets=secrets,
            method=method,
            path=path,
        )
        return schemas.ServiceTestResponse(
            success=True,
            message="Connection successful",
            http_status=int(result["http_status"]),
        )
    except ServiceHTTPError as exc:
        return schemas.ServiceTestResponse(
            success=False,
            message=exc.message,
            http_status=exc.http_status,
        )


@router.post("/test-config", response_model=schemas.ServiceTestResponse)
async def test_service_config(payload: schemas.ServiceCreate):
    return await _test_connection(
        service_id=None,
        service_type=payload.service_type,
        config=config_for_storage(payload.config),
        secrets=_secret_payload(payload.secrets),
    )


@router.post("/{service_id}/test", response_model=schemas.ServiceTestResponse)
async def test_service_connection(service_id: int, db: Session = Depends(get_db)):
    service = _query_services(db).filter(models.Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    return await _test_connection(
        service_id=service.id,
        service_type=service.service_type,
        config=service.config or {},
        secrets=service.secrets,
    )


@router.post("/{service_id}/test-config", response_model=schemas.ServiceTestResponse)
async def test_service_update_config(
    service_id: int,
    payload: schemas.ServiceUpdate,
    db: Session = Depends(get_db),
):
    service = _query_services(db).filter(models.Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    config = (
        config_for_storage(payload.config)
        if payload.config is not None
        else dict(service.config or {})
    )
    secrets = dict(service.secrets or {})
    supplied = _secret_payload(payload.secrets)
    if supplied:
        secrets.update({key: value for key, value in supplied.items() if value})
    elif payload.config is not None and payload.config.auth_type == "none":
        secrets = {}
    return await _test_connection(
        service_id=service.id,
        service_type=payload.service_type or service.service_type,
        config=config,
        secrets=secrets or None,
    )
