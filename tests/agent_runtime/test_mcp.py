from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager

import httpx
import mcp_types as mcp_types
import pytest
import uvicorn
from fastapi import FastAPI
from mcp.server.lowlevel import Server
from mcp.server.sse import SseServerTransport
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Mount, Route
from test_models import config
from test_runtime import Script, call

from app.api.agent_runs import create_run_router
from app.db.base import Base
from app.models.models import DataSource, Service
from app.services.agent.application import RuntimeApplication, local_actor
from app.services.agent.definitions import RunDependencies
from app.services.agent.models import ModelFactory
from app.services.integration.mcp_service import test_mcp_connection as check_mcp_connection
from app.services.platform.settings_store import upsert_setting


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@asynccontextmanager
async def running_mcp_server(transport_name: str):
    calls: list[dict] = []

    async def list_tools(_ctx, _params):
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

    async def call_tool(_ctx, params):
        arguments = dict(params.arguments or {})
        calls.append({"name": params.name, "arguments": arguments})
        marker = f"praxis-mcp-live-{arguments.get('seed', '')}"
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(text=marker)],
            structuredContent={"marker": marker},
        )

    mcp = Server(
        "praxis-real-mcp-test",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    port = _free_port()
    if transport_name == "streamable_http":
        app = mcp.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            host="127.0.0.1",
        )
        url = f"http://127.0.0.1:{port}/mcp"
    else:
        sse = SseServerTransport("/messages/")

        async def handle_sse(request):
            async with sse.connect_sse(
                request.scope,
                request.receive,
                request._send,
            ) as streams:
                await mcp.run(
                    streams[0],
                    streams[1],
                    mcp.create_initialization_options(),
                )
            return Response()

        app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse, methods=["GET"]),
                Mount("/messages/", app=sse.handle_post_message),
            ]
        )
        url = f"http://127.0.0.1:{port}/sse"

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    async with asyncio.timeout(5):
        while not server.started:
            if task.done():
                await task
            await asyncio.sleep(0.01)
    try:
        yield url, calls
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.parametrize("transport_name", ["streamable_http", "sse"])
async def test_real_mcp_connection_discovers_tools(transport_name):
    async with running_mcp_server(transport_name) as (url, _calls):
        result = await check_mcp_connection(
            service_id=7,
            config={
                "base_url": url,
                "mcp_transport": transport_name,
                "timeout_seconds": 5,
                "verify_tls": True,
                "use_environment_proxy": False,
            },
            secrets=None,
        )

    assert result.tools == ("resolve_marker",)


def test_mcp_tool_discovery_rechecks_current_binding(store):
    Base.metadata.create_all(store.sessions.kw["bind"])
    with store.sessions.begin() as db:
        datasource = DataSource(
            name="MCP scope",
            host="127.0.0.1",
            port=3306,
            db_type="mysql",
            database="app",
            cluster_key="mcp-binding",
            status="active",
        )
        db.add(datasource)
        db.flush()
        service = Service(
            name="Bound MCP",
            service_type="mcp",
            resource_ref="cluster:mcp-binding",
            config={
                "base_url": "http://127.0.0.1:9/mcp",
                "mcp_transport": "streamable_http",
            },
            status="active",
        )
        db.add(service)
        db.flush()
        datasource_id, service_id = datasource.id, service.id

    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    deps = RunDependencies(
        run_id="binding-run",
        conversation_id="binding-conversation",
        actor_id="local",
        scope={
            "datasource_ids": [datasource_id],
            "mcp_service_ids": [service_id],
        },
    )
    assert len(runtime.mcp_toolsets({"id": "binding-run"}, deps, "owner")) == 1

    with store.sessions.begin() as db:
        current = db.get(Service, service_id)
        assert current is not None
        current.resource_ref = "cluster:other"

    assert runtime.mcp_toolsets({"id": "binding-run"}, deps, "owner") == []


async def test_chat_invokes_real_streamable_http_mcp_tool(store):
    Base.metadata.create_all(store.sessions.kw["bind"])
    async with running_mcp_server("streamable_http") as (url, calls):
        with store.sessions.begin() as db:
            datasource = DataSource(
                name="MCP scope",
                host="127.0.0.1",
                port=3306,
                db_type="mysql",
                database="app",
                cluster_key="mcp-e2e",
                status="active",
            )
            db.add(datasource)
            db.flush()
            service = Service(
                name="Real MCP fixture",
                service_type="mcp",
                resource_ref="cluster:mcp-e2e",
                config={
                    "base_url": url,
                    "mcp_transport": "streamable_http",
                    "timeout_seconds": 5,
                    "verify_tls": True,
                    "use_environment_proxy": False,
                },
                status="active",
            )
            db.add(service)
            upsert_setting(db, "ai_action_confirmation_bypass", True)
            db.flush()
            datasource_id, service_id = datasource.id, service.id

        runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
        script = Script(
            [
                call(
                    f"mcp_{service_id}_resolve_marker",
                    '{"seed":"chat"}',
                    call_id="mcp-call-1",
                )
            ],
            ["真实 MCP 返回了 praxis-mcp-live-chat。"],
        )
        runtime.service.model_factory = script.factory
        app = FastAPI()
        app.include_router(
            create_run_router(
                runtime.service,
                actor_dependency=local_actor,
                resolve_definition=runtime.resolve,
            )
        )
        await runtime.start()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                conversation = (
                    await client.post(
                        "/api/v1/conversations",
                        json={
                            "scene": {
                                "datasource_ids": [datasource_id],
                                "mcp_service_ids": [service_id],
                            }
                        },
                    )
                ).json()
                response = await client.post(
                    f"/api/v1/conversations/{conversation['id']}/runs",
                    json={
                        "client_request_id": "mcp-e2e",
                        "prompt": "调用 MCP 工具并告诉我返回的 marker。",
                    },
                )
                assert response.status_code == 202, response.text
                run_id = response.json()["id"]
                async with asyncio.timeout(10):
                    while runtime.store.get(run_id, "local")["status"] not in {
                        "finished",
                        "failed",
                        "limited",
                        "interrupted",
                    }:
                        await asyncio.sleep(0.01)

            row = runtime.store.get(run_id, "local")
            assert row["status"] == "finished", row
            assert row["output"] == "真实 MCP 返回了 praxis-mcp-live-chat。"
            assert calls == [
                {"name": "resolve_marker", "arguments": {"seed": "chat"}}
            ]
            offered = {tool.name for tool in script.requests[0][1].function_tools}
            assert f"mcp_{service_id}_resolve_marker" in offered
            assert "call_service" not in offered
            assert "list_services" not in offered
            assert row["tool_calls"][0]["name"] == f"mcp_{service_id}_resolve_marker"
            assert row["tool_calls"][0]["status"] == "succeeded"
            assert row["approvals"][0]["decided_by"] == (
                "platform_confirmation_bypass"
            )
            returned = [
                part
                for message in script.requests[1][0]
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            assert "praxis-mcp-live-chat" in str(returned[0].content)
            with store.sessions() as db:
                assert db.scalar(
                    select(Service.id).where(Service.id == service_id)
                ) == service_id
        finally:
            await runtime.close()
