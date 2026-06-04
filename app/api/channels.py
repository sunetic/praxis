from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.services.platform.object_tools import ObjectToolError, ObjectToolService

router = APIRouter(prefix="/channels", tags=["Channels"])
_object_service = ObjectToolService()


def _raise_object_error(err: ObjectToolError) -> None:
    code = str(err.code or "")
    if code == "not_found":
        raise HTTPException(status_code=404, detail=err.message) from err
    raise HTTPException(
        status_code=400,
        detail={
            "code": err.code,
            "message": err.message,
            "details": err.details or {},
        },
    ) from err


@router.get("")
async def list_channels(
    provider: str | None = Query(default=None),
    status_text: str | None = Query(default=None, alias="status"),
):
    try:
        result = await _object_service.crud(
            object_type="channel",
            action="list",
            payload={},
            actor="api:channels",
        )
    except ObjectToolError as err:
        _raise_object_error(err)
    items = result.get("items") if isinstance(result, dict) else []
    channels = [item for item in items if isinstance(item, dict)]
    if provider:
        channels = [item for item in channels if str(item.get("provider") or "") == provider]
    if status_text:
        channels = [item for item in channels if str(item.get("status") or "") == status_text]
    return channels


@router.get("/{channel_id}")
async def get_channel(channel_id: int):
    try:
        return await _object_service.crud(
            object_type="channel",
            action="read",
            object_id=int(channel_id),
            payload={},
            actor="api:channels",
        )
    except ObjectToolError as err:
        _raise_object_error(err)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_channel(payload: dict[str, Any]):
    try:
        return await _object_service.crud(
            object_type="channel",
            action="create",
            payload=payload or {},
            actor="api:channels",
        )
    except ObjectToolError as err:
        _raise_object_error(err)


@router.patch("/{channel_id}")
async def update_channel(channel_id: int, payload: dict[str, Any]):
    try:
        return await _object_service.crud(
            object_type="channel",
            action="update",
            object_id=int(channel_id),
            payload=payload or {},
            actor="api:channels",
        )
    except ObjectToolError as err:
        _raise_object_error(err)


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(channel_id: int):
    try:
        await _object_service.crud(
            object_type="channel",
            action="delete",
            object_id=int(channel_id),
            payload={},
            actor="api:channels",
        )
    except ObjectToolError as err:
        _raise_object_error(err)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{channel_id}/send")
async def send_channel_message(channel_id: int, payload: dict[str, Any] | None = None):
    try:
        return await _object_service.operate(
            object_type="channel",
            action="send",
            object_id=int(channel_id),
            payload=payload or {},
            actor="api:channels",
        )
    except ObjectToolError as err:
        _raise_object_error(err)


@router.post("/{channel_id}/send-test")
async def send_channel_test_message(channel_id: int, payload: dict[str, Any] | None = None):
    return await send_channel_message(channel_id, payload)
