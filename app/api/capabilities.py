from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.logging import get_logger
from app.tools.registry import registry as tool_registry

router = APIRouter(prefix="/capabilities", tags=["Capabilities"])
logger = get_logger("api.capabilities")


@router.get("")
def list_capabilities() -> dict[str, Any]:
    tools = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }
        for tool in tool_registry.list_tools()
    ]

    logger.info("list_capabilities tools=%d", len(tools))
    return {"tools": tools}
