"""New run protocol. Mounted by the application when scene/auth wiring is ready."""

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.services.agent.definitions import AgentDefinition
from app.services.agent.models import ModelSnapshot
from app.services.agent.persistence import run_db
from app.services.agent.service import AgentRunService
from app.services.agent.store import RunConflictError, RunNotFoundError


class CreateRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=100_000)
    scene: dict = Field(default_factory=dict)
    mode: Literal["append", "stop_and_modify"] = "append"


class CreateConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="New conversation", min_length=1, max_length=500)
    scene: dict = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: StrictBool


class UpdateConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene: dict


class ReconcileCall(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolution: Literal["succeeded", "failed", "not_executed"]
    evidence: str = Field(min_length=1, max_length=10000)
    execution_stopped: StrictBool


class ResumeRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_event_seq: int = Field(strict=True, ge=0)


def _public_run(row: dict) -> dict:
    public = {
        key: row[key]
        for key in (
            "id",
            "conversation_id",
            "status",
            "cancel_requested",
            "output",
            "error_code",
            "event_seq",
            "prompt",
            "seq",
            "created_at",
            "budget",
            "tool_calls",
            "approvals",
        )
        if key in row
    }
    snapshot = row.get("model_snapshot")
    public["model"] = (
        ModelSnapshot.model_validate(snapshot).configuration.model_dump(mode="json")
        if snapshot
        else None
    )
    return public


def create_run_router(
    service: AgentRunService,
    *,
    actor_dependency: Callable,
    resolve_definition: Callable[[str | None, str, dict], AgentDefinition],
) -> APIRouter:
    """Identity and scene capability resolution must come from trusted server code."""
    router = APIRouter(prefix="/api/v1", tags=["Agent runs"])

    async def invoke(operation):
        try:
            return await run_db(operation)
        except RunNotFoundError as exc:
            raise HTTPException(404, "Resource not found") from exc
        except RunConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, "Invalid run request") from exc

    @router.post("/conversations", status_code=201)
    async def create_conversation(
        body: CreateConversation, actor_id: str = Depends(actor_dependency)
    ):
        def create():
            resolve_definition(None, actor_id, body.scene)
            return service.store.create_conversation(
                uuid.uuid4().hex,
                actor_id,
                title=body.title,
                scene=body.scene,
            )

        return await invoke(create)

    @router.get("/conversations")
    async def list_conversations(actor_id: str = Depends(actor_dependency)):
        return await run_db(service.store.list_conversations, actor_id)

    @router.get("/conversations/{conversation_id}")
    async def conversation(conversation_id: str, actor_id: str = Depends(actor_dependency)):
        return await invoke(lambda: service.store.get_conversation(conversation_id, actor_id))

    @router.get("/conversations/{conversation_id}/runs")
    async def list_runs(conversation_id: str, actor_id: str = Depends(actor_dependency)):
        return [
            _public_run(row)
            for row in await invoke(lambda: service.store.list_runs(conversation_id, actor_id))
        ]

    @router.patch("/conversations/{conversation_id}")
    async def update_conversation(
        conversation_id: str, body: UpdateConversation, actor_id: str = Depends(actor_dependency)
    ):
        def save():
            current = service.store.get_conversation(conversation_id, actor_id)
            scene = {**current["scene"], **body.scene}
            scope_keys = {
                "agent_id",
                "datasource_ids",
                "knowledge_base_ids",
                "service_ids",
                "function_ids",
                "page_ids",
                "skill_draft_ids",
                "skills",
            }
            if any(
                key in body.scene and body.scene[key] != current["scene"].get(key)
                for key in scope_keys
            ):
                # A grant never survives a scope change, even if a later update
                # switches back to the previous resource selection.
                scene["auto_approval"] = None
            resolve_definition(conversation_id, actor_id, scene)
            return service.store.update_conversation(conversation_id, actor_id, scene=scene)

        return await invoke(save)

    @router.post("/conversations/{conversation_id}/runs", status_code=202)
    async def submit(
        conversation_id: str, body: CreateRun, actor_id: str = Depends(actor_dependency)
    ):
        def create():
            # Auto-approval must be persisted through the explicit conversation
            # setting first; a run submission cannot smuggle in a new grant.
            run_scene = {key: value for key, value in body.scene.items() if key != "auto_approval"}
            definition = resolve_definition(conversation_id, actor_id, run_scene)
            return service.submit(
                conversation_id,
                actor_id,
                body.client_request_id,
                body.prompt,
                definition,
                stop_and_modify=body.mode == "stop_and_modify",
            )

        return _public_run(await invoke(create))

    @router.get("/runs/{run_id}")
    async def get(run_id: str, actor_id: str = Depends(actor_dependency)):
        return _public_run(await invoke(lambda: service.store.get(run_id, actor_id)))

    @router.post("/runs/{run_id}/cancel")
    async def cancel(run_id: str, actor_id: str = Depends(actor_dependency)):
        return _public_run(await invoke(lambda: service.cancel(run_id, actor_id)))

    @router.post("/runs/{run_id}/tool-calls/{call_id}/approval")
    async def decide(
        run_id: str, call_id: str, body: ApprovalDecision, actor_id: str = Depends(actor_dependency)
    ):
        return await invoke(
            lambda: service.approve(run_id, actor_id, call_id, body.fingerprint, body.approved)
        )

    @router.post("/runs/{run_id}/tool-calls/{call_id}/reconciliation")
    async def reconcile(
        run_id: str, call_id: str, body: ReconcileCall, actor_id: str = Depends(actor_dependency)
    ):
        return await invoke(
            lambda: service.store.reconcile(
                run_id,
                actor_id,
                call_id,
                body.fingerprint,
                resolution=body.resolution,
                evidence=body.evidence,
                execution_stopped=body.execution_stopped,
            )
        )

    @router.post("/runs/{run_id}/resume", status_code=202)
    async def resume(run_id: str, body: ResumeRun, actor_id: str = Depends(actor_dependency)):
        return _public_run(
            await invoke(lambda: service.resume(run_id, actor_id, body.expected_event_seq))
        )

    @router.get("/runs/{run_id}/events")
    async def events(
        run_id: str,
        actor_id: str = Depends(actor_dependency),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        row = await invoke(lambda: service.store.get(run_id, actor_id))
        try:
            after = int(last_event_id or "0")
            if after < 0 or after > row["event_seq"]:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(400, "Invalid event cursor") from exc

        async def stream():
            subscription = service.subscribe(run_id, actor_id, after)
            pending = asyncio.create_task(anext(subscription))
            try:
                while True:
                    ready, _ = await asyncio.wait({pending}, timeout=15)
                    if not ready:
                        yield ": heartbeat\n\n"
                        continue
                    try:
                        event = pending.result()
                    except StopAsyncIteration:
                        return
                    payload = json.dumps(
                        {
                            "type": event["kind"],
                            "run_id": run_id,
                            "seq": event["seq"],
                            "created_at": event["created_at"],
                            **event["payload"],
                        },
                        ensure_ascii=False,
                    )
                    yield f"id: {event['seq']}\nevent: {event['kind']}\ndata: {payload}\n\n"
                    pending = asyncio.create_task(anext(subscription))
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                await subscription.aclose()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
