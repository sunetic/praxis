from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.logging import get_logger

router = APIRouter(prefix="/capabilities", tags=["Capabilities"])
logger = get_logger("api.capabilities")


@router.get("")
def list_capabilities(request: Request) -> dict[str, Any]:
    tools = [
        {
            "name": entry.tool.name,
            "description": entry.tool.description,
            "parameters": entry.tool.tool_def.parameters_json_schema,
        }
        for _name, entry in sorted(request.app.state.agent_runtime.tools.items())
    ]

    logger.info("list_capabilities tools=%d", len(tools))
    return {"tools": tools}
