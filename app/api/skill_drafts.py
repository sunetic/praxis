"""Persistent Skill workspaces. Installing a Skill remains an explicit user operation."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.services.agent.application import local_actor
from app.services.agent.persistence import run_db
from app.services.agent.store import RunConflictError, RunNotFoundError
from app.services.skill.native_authoring import (
    DraftID,
    Revision,
    SkillDraftContent,
    SkillDraftStore,
)

router = APIRouter(prefix="/skill-drafts", tags=["Skill drafts"])


class WriteDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: Revision
    content: SkillDraftContent


async def invoke(request, operation):
    store = SkillDraftStore(request.app.state.agent_runtime.sessions)
    try:
        return await run_db(operation, store)
    except RunNotFoundError as exc:
        raise HTTPException(404, "Skill draft not found") from exc
    except RunConflictError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("", status_code=201)
async def create(request: Request, actor_id: str = Depends(local_actor)):
    return await invoke(request, lambda store: store.create(actor_id))


@router.get("/{draft_id}")
async def read(draft_id: DraftID, request: Request, actor_id: str = Depends(local_actor)):
    return await invoke(request, lambda store: store.read(draft_id, actor_id))


@router.patch("/{draft_id}")
async def write(
    draft_id: DraftID, body: WriteDraft, request: Request, actor_id: str = Depends(local_actor)
):
    return await invoke(
        request,
        lambda store: store.write(
            draft_id,
            actor_id,
            expected_revision=body.expected_revision,
            content=body.content,
        ),
    )
