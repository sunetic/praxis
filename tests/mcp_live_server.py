"""Deterministic Streamable HTTP MCP server for live browser acceptance tests."""

from __future__ import annotations

import os
from typing import Any

import mcp_types
import uvicorn
from mcp.server.lowlevel import Server
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

calls: list[dict[str, Any]] = []


async def list_tools(_context: Any, _params: Any) -> mcp_types.ListToolsResult:
    return mcp_types.ListToolsResult(
        tools=[
            mcp_types.Tool(
                name="resolve_marker",
                description="Return a deterministic marker for the supplied seed.",
                inputSchema={
                    "type": "object",
                    "properties": {"seed": {"type": "string"}},
                    "required": ["seed"],
                    "additionalProperties": False,
                },
            )
        ]
    )


async def call_tool(_context: Any, params: Any) -> mcp_types.CallToolResult:
    arguments = dict(params.arguments or {})
    calls.append({"name": params.name, "arguments": arguments})
    marker = f"praxis-browser-mcp-{arguments.get('seed', '')}"
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(text=marker)],
        structuredContent={"marker": marker},
    )


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def observed_calls(_request: Request) -> JSONResponse:
    return JSONResponse(calls)


server = Server(
    "praxis-browser-mcp-test",
    version="1.0.0",
    on_list_tools=list_tools,
    on_call_tool=call_tool,
)
app = server.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    host="0.0.0.0",
)
app.routes.extend(
    [
        Route("/health", health, methods=["GET"]),
        Route("/calls", observed_calls, methods=["GET"]),
    ]
)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8765")))
