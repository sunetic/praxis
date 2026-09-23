from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, selectinload

from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models import models
from app.schemas import schemas
from app.skills.store import skill_store

router = APIRouter(prefix="/agents", tags=["Agents"])
logger = get_logger("api.agents")


def _to_agent_response(agent: models.Agent) -> schemas.AgentResponse:
    return schemas.AgentResponse.model_validate(agent)


def _validate_skill_names(skill_names: list[str] | None) -> None:
    if skill_names is None:
        return
    existing = {item.name for item in skill_store.load()}
    missing = sorted({name for name in skill_names if name not in existing})
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown skills: {', '.join(missing)}",
        )


def _validate_tools(request: Request, names: list[str] | None) -> None:
    unknown = set(names or []) - request.app.state.agent_runtime.tools.keys()
    if unknown:
        raise HTTPException(422, f"Unknown tools: {', '.join(sorted(unknown))}")


def _datasources(db: Session, ids: list[int]) -> list[models.DataSource]:
    records = (
        db.query(models.DataSource)
        .filter(models.DataSource.id.in_(ids), models.DataSource.status == "active")
        .all()
    )
    if {item.id for item in records} != set(ids):
        raise HTTPException(422, "Datasource selection contains unavailable resources")
    return records


@router.get("", response_model=list[schemas.AgentResponse])
def list_agents(db: Session = Depends(get_db)):
    agents = db.query(models.Agent).options(selectinload(models.Agent.datasources)).all()
    logger.info("list_agents %s", fmt_kv(count=len(agents)))
    return [_to_agent_response(agent) for agent in agents]


@router.get("/{agent_id}", response_model=schemas.AgentResponse)
def get_agent(agent_id: int, db: Session = Depends(get_db)):
    agent = db.query(models.Agent).filter(models.Agent.id == agent_id).first()
    if not agent:
        logger.warning("get_agent_not_found %s", fmt_kv(agent_id=agent_id))
        raise HTTPException(status_code=404, detail="Agent not found")
    logger.info("get_agent %s", fmt_kv(agent_id=agent_id))
    return _to_agent_response(agent)


@router.post("", response_model=schemas.AgentResponse, status_code=status.HTTP_201_CREATED)
def create_agent(agent: schemas.AgentCreate, request: Request, db: Session = Depends(get_db)):
    agent_data = agent.model_dump()
    datasource_ids = agent_data.pop("datasource_ids")
    # Agents created from user-facing UI are custom agents by default.
    agent_data["agent_type"] = "custom"
    _validate_skill_names(agent_data.get("skills"))
    _validate_tools(request, agent_data.get("tools"))

    db_agent = models.Agent(**agent_data, datasources=_datasources(db, datasource_ids))
    db.add(db_agent)
    db.flush()

    db.commit()
    db.refresh(db_agent)
    logger.info(
        "create_agent %s",
        fmt_kv(agent_id=db_agent.id),
    )
    return _to_agent_response(db_agent)


@router.patch("/{agent_id}", response_model=schemas.AgentResponse)
def update_agent(
    agent_id: int,
    agent_update: schemas.AgentUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    db_agent = db.query(models.Agent).filter(models.Agent.id == agent_id).first()
    if not db_agent:
        logger.warning("update_agent_not_found %s", fmt_kv(agent_id=agent_id))
        raise HTTPException(status_code=404, detail="Agent not found")

    update_data = agent_update.model_dump(exclude_unset=True)
    if "skills" in update_data:
        _validate_skill_names(update_data.get("skills"))
    if "tools" in update_data:
        _validate_tools(request, update_data["tools"])
    if "datasource_ids" in update_data:
        ids = update_data.pop("datasource_ids")
        if ids is None:
            raise HTTPException(422, "Use an empty list to revoke all datasource access")
        db_agent.datasources = _datasources(db, ids)

    for field, value in update_data.items():
        setattr(db_agent, field, value)

    db.commit()
    db.refresh(db_agent)
    logger.info(
        "update_agent %s",
        fmt_kv(agent_id=agent_id),
    )
    return _to_agent_response(db_agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent(agent_id: int, db: Session = Depends(get_db)):
    db_agent = db.query(models.Agent).filter(models.Agent.id == agent_id).first()
    if not db_agent:
        logger.warning("delete_agent_not_found %s", fmt_kv(agent_id=agent_id))
        raise HTTPException(status_code=404, detail="Agent not found")

    db.delete(db_agent)
    db.commit()
    logger.info("delete_agent %s", fmt_kv(agent_id=agent_id))
    return None
