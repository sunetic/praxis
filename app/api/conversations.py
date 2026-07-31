import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models import models
from app.schemas import schemas
from app.skills.store import skill_store

router = APIRouter(prefix="/conversations", tags=["Conversations"])
logger = get_logger("api.conversations")
settings = get_settings()


def _validate_active_skills(active_skills: list[str] | None) -> None:
    if active_skills is None:
        return
    skill_store.load()
    available = {item.name for item in skill_store.list_skills()}
    missing = sorted({name for name in active_skills if name not in available})
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown active skills: {', '.join(missing)}")


@router.get("", response_model=list[schemas.ConversationResponse])
def list_conversations(
    datasource_id: int | None = None,
    agent_id: int | None = None,
    category: str | None = None,
    scene_key: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(models.Conversation)
    if datasource_id:
        query = query.filter(models.Conversation.datasource_id == datasource_id)
    if agent_id:
        query = query.filter(models.Conversation.agent_id == agent_id)
    if category:
        normalized_category = schemas._normalize_conversation_category(category)
        query = query.filter(models.Conversation.category == normalized_category)
    if scene_key:
        query = query.filter(models.Conversation.scene_key == scene_key)
    records = query.order_by(models.Conversation.updated_at.desc()).all()
    logger.info(
        "list_conversations %s",
        fmt_kv(
            datasource_id=datasource_id,
            agent_id=agent_id,
            category=category,
            scene_key=scene_key,
            count=len(records),
        ),
    )
    return records


@router.get("/{conversation_id}", response_model=schemas.ConversationResponse)
def get_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conversation = (
        db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    )
    if not conversation:
        logger.warning("get_conversation_not_found %s", fmt_kv(conversation_id=conversation_id))
        raise HTTPException(status_code=404, detail="Conversation not found")
    logger.info("get_conversation %s", fmt_kv(conversation_id=conversation_id))
    return conversation


@router.post("", response_model=schemas.ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(conversation: schemas.ConversationCreate, db: Session = Depends(get_db)):
    _validate_active_skills(conversation.active_skills)
    db_conversation = models.Conversation(**conversation.model_dump())
    db.add(db_conversation)
    db.commit()
    db.refresh(db_conversation)
    logger.info(
        "create_conversation %s",
        fmt_kv(
            conversation_id=db_conversation.id,
            datasource_id=db_conversation.datasource_id,
            agent_id=db_conversation.agent_id,
            active_skill_count=len(db_conversation.active_skills or []),
        ),
    )
    return db_conversation


@router.patch("/{conversation_id}", response_model=schemas.ConversationResponse)
def update_conversation(
    conversation_id: int,
    conversation_update: schemas.ConversationUpdate,
    db: Session = Depends(get_db),
):
    db_conversation = (
        db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    )
    if not db_conversation:
        logger.warning("update_conversation_not_found %s", fmt_kv(conversation_id=conversation_id))
        raise HTTPException(status_code=404, detail="Conversation not found")

    update_data = conversation_update.model_dump(exclude_unset=True)
    if "active_skills" in update_data:
        _validate_active_skills(update_data.get("active_skills"))
    for field, value in update_data.items():
        setattr(db_conversation, field, value)

    db.commit()
    db.refresh(db_conversation)
    logger.info(
        "update_conversation %s",
        fmt_kv(
            conversation_id=conversation_id,
            datasource_id=db_conversation.datasource_id,
            agent_id=db_conversation.agent_id,
            active_skill_count=len(db_conversation.active_skills or []),
        ),
    )
    return db_conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: int, db: Session = Depends(get_db)):
    db_conversation = (
        db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    )
    if not db_conversation:
        logger.warning("delete_conversation_not_found %s", fmt_kv(conversation_id=conversation_id))
        raise HTTPException(status_code=404, detail="Conversation not found")

    db.delete(db_conversation)
    db.commit()
    logger.info("delete_conversation %s", fmt_kv(conversation_id=conversation_id))
    return None


message_router = APIRouter(prefix="/messages", tags=["Messages"])
message_logger = get_logger("api.messages")


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _ensure_builder_runtime_enabled() -> None:
    if not settings.builder_runtime_enabled:
        raise HTTPException(status_code=403, detail="Builder runtime is disabled by feature flag")


def _cleanup_expired_build_sessions(db: Session, now: datetime) -> None:
    (
        db.query(models.BuildSession)
        .filter(
            models.BuildSession.status == "active",
            models.BuildSession.expires_at <= now,
        )
        .update({"status": "closed", "updated_at": now}, synchronize_session=False)
    )


@message_router.get("/conversation/{conversation_id}", response_model=list[schemas.MessageResponse])
def get_messages(conversation_id: int, db: Session = Depends(get_db)):
    conversation = (
        db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    )
    if not conversation:
        message_logger.warning("get_messages_not_found %s", fmt_kv(conversation_id=conversation_id))
        raise HTTPException(status_code=404, detail="Conversation not found")
    records = (
        db.query(models.Message)
        .filter(
            models.Message.conversation_id == conversation_id,
            models.Message.role.in_(["user", "assistant"]),
        )
        .order_by(models.Message.created_at.asc())
        .all()
    )
    message_logger.info(
        "get_messages %s", fmt_kv(conversation_id=conversation_id, count=len(records))
    )
    return records


@message_router.post(
    "", response_model=schemas.MessageResponse, status_code=status.HTTP_201_CREATED
)
def create_message(message: schemas.MessageCreate, db: Session = Depends(get_db)):
    db_message = models.Message(**message.model_dump())
    db.add(db_message)
    db.flush()  # Flush to get the created_at value without full commit

    latest_turn_event = (
        db.query(models.ChatEvent)
        .filter(
            models.ChatEvent.conversation_id == message.conversation_id,
            models.ChatEvent.turn_seq.is_not(None),
        )
        .order_by(
            models.ChatEvent.turn_seq.desc(),
            models.ChatEvent.part_seq.desc(),
            models.ChatEvent.id.desc(),
        )
        .first()
    )
    next_turn_seq = int(latest_turn_event.turn_seq or 0) + 1 if latest_turn_event else 1
    timeline_event_type = "user_message" if message.role == "user" else "assistant"
    db.add(
        models.ChatEvent(
            conversation_id=message.conversation_id,
            event_type=timeline_event_type,
            phase="message_created",
            turn_id=f"message-{db_message.id}-{uuid.uuid4().hex[:8]}",
            turn_seq=next_turn_seq,
            part_seq=0,
            role=message.role,
            agent_name=getattr(db_message, "agent_name", None),
            payload={
                "content": db_message.content,
                "message_id": db_message.id,
                "event_kind": timeline_event_type,
            },
            created_at=db_message.created_at,
        )
    )

    conversation = (
        db.query(models.Conversation)
        .filter(models.Conversation.id == message.conversation_id)
        .first()
    )
    if conversation:
        conversation.updated_at = db_message.created_at

    db.commit()
    db.refresh(db_message)
    message_logger.info(
        "create_message %s",
        fmt_kv(
            message_id=db_message.id,
            conversation_id=message.conversation_id,
            role=message.role,
            turn_seq=next_turn_seq,
        ),
    )
    return db_message


@router.post(
    "/{conversation_id}/build-sessions",
    response_model=schemas.BuildSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_build_session(
    conversation_id: int,
    request: schemas.BuildSessionCreate,
    db: Session = Depends(get_db),
):
    _ensure_builder_runtime_enabled()
    conversation = (
        db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    now = _utc_now_naive()
    _cleanup_expired_build_sessions(db, now)
    (
        db.query(models.BuildSession)
        .filter(
            models.BuildSession.conversation_id == conversation_id,
            models.BuildSession.status == "active",
        )
        .update({"status": "closed", "updated_at": now}, synchronize_session=False)
    )
    session = models.BuildSession(
        conversation_id=conversation_id,
        scope_type="builder",
        scope_object_type=request.scope_object_type,
        scope_object_id=request.scope_object_id,
        ttl_seconds=request.ttl_seconds,
        heartbeat_at=now,
        expires_at=now + timedelta(seconds=request.ttl_seconds),
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    logger.info(
        "create_build_session %s",
        fmt_kv(
            conversation_id=conversation_id,
            build_session_id=session.id,
            scope_object_type=session.scope_object_type,
            scope_object_id=session.scope_object_id,
        ),
    )
    return session


@router.get(
    "/{conversation_id}/build-sessions/active",
    response_model=schemas.BuildSessionResponse,
)
def get_active_build_session(conversation_id: int, db: Session = Depends(get_db)):
    _ensure_builder_runtime_enabled()
    now = _utc_now_naive()
    _cleanup_expired_build_sessions(db, now)
    session = (
        db.query(models.BuildSession)
        .filter(
            models.BuildSession.conversation_id == conversation_id,
            models.BuildSession.status == "active",
            models.BuildSession.expires_at > now,
        )
        .order_by(models.BuildSession.updated_at.desc())
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Active build session not found")
    return session


@router.post(
    "/{conversation_id}/build-sessions/{session_id}/heartbeat",
    response_model=schemas.BuildSessionResponse,
)
def heartbeat_build_session(
    conversation_id: int,
    session_id: int,
    request: schemas.BuildSessionHeartbeat,
    db: Session = Depends(get_db),
):
    _ensure_builder_runtime_enabled()
    session = (
        db.query(models.BuildSession)
        .filter(
            models.BuildSession.id == session_id,
            models.BuildSession.conversation_id == conversation_id,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Build session not found")
    if session.status != "active":
        raise HTTPException(status_code=400, detail="Build session is not active")

    now = _utc_now_naive()
    _cleanup_expired_build_sessions(db, now)
    ttl = request.ttl_seconds or session.ttl_seconds
    session.ttl_seconds = ttl
    session.heartbeat_at = now
    session.expires_at = now + timedelta(seconds=ttl)
    session.updated_at = now
    db.commit()
    db.refresh(session)
    return session


@router.delete(
    "/{conversation_id}/build-sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def close_build_session(conversation_id: int, session_id: int, db: Session = Depends(get_db)):
    _ensure_builder_runtime_enabled()
    session = (
        db.query(models.BuildSession)
        .filter(
            models.BuildSession.id == session_id,
            models.BuildSession.conversation_id == conversation_id,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Build session not found")
    session.status = "closed"
    session.updated_at = _utc_now_naive()
    db.commit()
    return None
