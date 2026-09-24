"""Skill workspaces are drafts, never installed instructions or completion signals."""

import time
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.models.artifacts import skill_drafts
from app.services.agent.store import RunConflictError, RunNotFoundError, fingerprint

DraftID = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Revision = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SkillDraftContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=64)
    version: str = Field(default="1.0.0", max_length=64)
    description: str = Field(default="", max_length=4000)
    database: Literal["general", "mysql", "postgresql", "oceanbase"] = "general"
    always_apply: bool = False
    prompt: str = Field(default="", max_length=100_000)


class SkillDraftChanges(BaseModel):
    """An edit supplies only changed fields; null means leave the field unchanged."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=4000)
    database: Literal["general", "mysql", "postgresql", "oceanbase"] | None = None
    always_apply: bool | None = None
    prompt: str | None = Field(default=None, max_length=100_000)


class SkillDraftStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def create(self, actor_id):
        row = dict(
            id=uuid.uuid4().hex,
            actor_id=actor_id,
            revision=fingerprint(uuid.uuid4().hex),
            content=SkillDraftContent().model_dump(),
            run_id=None,
            updated_at=time.time(),
        )
        with self.sessions.begin() as db:
            db.execute(insert(skill_drafts).values(**row))
        return row

    def read(self, draft_id: DraftID, actor_id: str) -> dict[str, Any]:
        with self.sessions() as db:
            row = (
                db.execute(
                    select(skill_drafts).where(
                        skill_drafts.c.id == draft_id,
                        skill_drafts.c.actor_id == actor_id,
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise RunNotFoundError("Skill draft not found")
            return dict(row)

    def write(
        self, draft_id, actor_id, *, expected_revision, content: SkillDraftContent, run_id=None
    ):
        self.read(draft_id, actor_id)
        revision = fingerprint(uuid.uuid4().hex)
        with self.sessions.begin() as db:
            row = (
                db.execute(
                    update(skill_drafts)
                    .where(
                        skill_drafts.c.id == draft_id,
                        skill_drafts.c.actor_id == actor_id,
                        skill_drafts.c.revision == expected_revision,
                    )
                    .values(
                        content=content.model_dump(mode="json"),
                        revision=revision,
                        run_id=run_id,
                        updated_at=time.time(),
                    )
                    .returning(skill_drafts)
                )
                .mappings()
                .first()
            )
            if row is None:
                raise RunConflictError(
                    "Skill draft changed; read the current revision before editing"
                )
            result = dict(row)
        return result
