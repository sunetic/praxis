from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

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
    skill_store.load()
    existing = {item.name for item in skill_store.list_skills()}
    missing = sorted({name for name in skill_names if name not in existing})
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown skills: {', '.join(missing)}",
        )


@router.get("", response_model=List[schemas.AgentResponse])
def list_agents(db: Session = Depends(get_db)):
    agents = db.query(models.Agent).all()
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
def create_agent(agent: schemas.AgentCreate, db: Session = Depends(get_db)):
    agent_data = agent.model_dump()
    # Agents created from user-facing UI are custom agents by default.
    agent_data["agent_type"] = "custom"
    _validate_skill_names(agent_data.get("skills"))

    db_agent = models.Agent(**agent_data)
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
    db: Session = Depends(get_db),
):
    db_agent = db.query(models.Agent).filter(models.Agent.id == agent_id).first()
    if not db_agent:
        logger.warning("update_agent_not_found %s", fmt_kv(agent_id=agent_id))
        raise HTTPException(status_code=404, detail="Agent not found")

    update_data = agent_update.model_dump(exclude_unset=True)
    if "skills" in update_data:
        _validate_skill_names(update_data.get("skills"))

    for field, value in update_data.items():
        setattr(db_agent, field, value)

    db.commit()
    db.refresh(db_agent)
    logger.info(
        "update_agent %s",
        fmt_kv(agent_id=agent_id),
    )
    return _to_agent_response(db_agent)


@router.post("/{agent_id}/run", response_model=schemas.AgentRunResponse, status_code=status.HTTP_201_CREATED)
def run_agent(
    agent_id: int,
    request: schemas.AgentRunRequest,
    db: Session = Depends(get_db),
):
    db_agent = db.query(models.Agent).filter(models.Agent.id == agent_id).first()
    if not db_agent:
        logger.warning("run_agent_not_found %s", fmt_kv(agent_id=agent_id))
        raise HTTPException(status_code=404, detail="Agent not found")

    if db_agent.status != "active":
        logger.warning("run_agent_inactive %s", fmt_kv(agent_id=agent_id, status=db_agent.status))
        raise HTTPException(status_code=400, detail="Agent is not active")

    datasource_ids = request.datasource_ids or []
    selected_ids: list[int] = []
    if datasource_ids:
        selected_records = (
            db.query(models.DataSource)
            .filter(models.DataSource.id.in_(datasource_ids))
            .all()
        )
        by_id = {record.id: record for record in selected_records}
        missing_ids = [item for item in datasource_ids if item not in by_id]
        if missing_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown datasource ids: {', '.join(str(item) for item in missing_ids)}",
            )
        inactive_ids = [
            item for item in datasource_ids if str(by_id[item].status or "").lower() != "active"
        ]
        if inactive_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Datasource not active: {', '.join(str(item) for item in inactive_ids)}",
            )
        selected_ids = [item for item in datasource_ids if item in by_id]

    initial_datasource_id = selected_ids[0] if selected_ids else None
    title = request.title or f"{db_agent.name} run session"
    db_conversation = models.Conversation(
        title=title,
        datasource_id=initial_datasource_id,
        agent_id=db_agent.id,
        active_skills=list(db_agent.skills or []),
    )
    db.add(db_conversation)
    db.commit()
    db.refresh(db_conversation)

    logger.info(
        "run_agent %s",
        fmt_kv(
            agent_id=db_agent.id,
            conversation_id=db_conversation.id,
            datasource_count=len(selected_ids),
            initial_datasource_id=initial_datasource_id,
        ),
    )
    return schemas.AgentRunResponse(
        conversation=schemas.ConversationResponse.model_validate(db_conversation),
        datasource_ids=selected_ids,
    )


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
