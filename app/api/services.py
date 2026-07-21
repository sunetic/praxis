from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models import models
from app.schemas import schemas

router = APIRouter(prefix="/services", tags=["Services"])
logger = get_logger("api.services")


@router.get("", response_model=List[schemas.ServiceResponse])
def list_services(db: Session = Depends(get_db)):
    records = db.query(models.Service).all()
    logger.info("list_services %s", fmt_kv(count=len(records)))
    return records


@router.get("/{service_id}", response_model=schemas.ServiceResponse)
def get_service(service_id: int, db: Session = Depends(get_db)):
    service = db.query(models.Service).filter(models.Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    return service


@router.post("", response_model=schemas.ServiceResponse, status_code=status.HTTP_201_CREATED)
def create_service(payload: schemas.ServiceCreate, db: Session = Depends(get_db)):
    db_service = models.Service(**payload.model_dump())
    db.add(db_service)
    db.commit()
    db.refresh(db_service)
    logger.info(
        "create_service %s",
        fmt_kv(service_id=db_service.id, name=db_service.name, service_type=db_service.service_type),
    )
    return db_service


@router.patch("/{service_id}", response_model=schemas.ServiceResponse)
def update_service(
    service_id: int,
    payload: schemas.ServiceUpdate,
    db: Session = Depends(get_db),
):
    db_service = db.query(models.Service).filter(models.Service.id == service_id).first()
    if not db_service:
        raise HTTPException(status_code=404, detail="Service not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(db_service, field, value)

    db.commit()
    db.refresh(db_service)
    logger.info("update_service %s", fmt_kv(service_id=service_id))
    return db_service


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(service_id: int, db: Session = Depends(get_db)):
    db_service = db.query(models.Service).filter(models.Service.id == service_id).first()
    if not db_service:
        raise HTTPException(status_code=404, detail="Service not found")
    db.delete(db_service)
    db.commit()
    logger.info("delete_service %s", fmt_kv(service_id=service_id))
    return None


@router.post("/test-config")
async def test_service_config(payload: schemas.ServiceCreate):
    import httpx

    config = payload.config or {}
    host = config.get("host", "")
    port = config.get("port", 8080)
    user = config.get("user", "")
    password = config.get("password", "")

    if not host:
        return {"success": False, "message": "Config missing 'host'"}

    base_url = f"http://{host}:{port}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/api/v2/time",
                auth=(user, password) if user else None,
            )
            resp.raise_for_status()
            return {"success": True, "message": "Connection successful"}
    except httpx.HTTPStatusError as e:
        return {"success": False, "message": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/{service_id}/test")
async def test_service_connection(service_id: int, db: Session = Depends(get_db)):
    import httpx

    service = db.query(models.Service).filter(models.Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    config = service.config or {}
    host = config.get("host", "")
    port = config.get("port", 8080)
    user = config.get("user", "")
    password = config.get("password", "")

    if not host:
        return {"success": False, "message": "Service config missing 'host'"}

    base_url = f"http://{host}:{port}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/api/v2/time",
                auth=(user, password) if user else None,
            )
            resp.raise_for_status()
            return {"success": True, "message": "Connection successful"}
    except httpx.HTTPStatusError as e:
        return {"success": False, "message": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"success": False, "message": str(e)}
