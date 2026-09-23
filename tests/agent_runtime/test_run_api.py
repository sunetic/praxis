import json

import httpx
from fastapi import FastAPI, Header
from pydantic_ai import Tool
from test_runtime import Script, call
from test_service import approve_write, settled

from app.api.agent_runs import create_run_router
from app.services.agent.definitions import AgentDefinition
from app.services.agent.execution import RegisteredTool
from app.services.agent.service import AgentRunService


def app_for(service):
    def actor(x_actor: str = Header(default="user")):
        return x_actor

    app = FastAPI()
    app.include_router(
        create_run_router(
            service,
            actor_dependency=actor,
            resolve_definition=lambda _conversation, _actor, _scene: AgentDefinition(
                name="api-test", tool_names=frozenset(service.tools)
            ),
        )
    )
    return app


async def test_create_get_cancel_and_event_cursor_protocol(store):
    script = Script(["你好。"])
    service = AgentRunService(
        store,
        script.factory,
        {},
        capabilities_for_run=lambda _: frozenset(),
        poll_seconds=0.01,
    )
    await service.start()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_for(service)), base_url="http://test"
        ) as client:
            body = {"client_request_id": "one", "prompt": "问候"}
            created = await client.post("/api/v1/conversations/conversation/runs", json=body)
            assert created.status_code == 202
            run_id = created.json()["id"]
            duplicate = await client.post("/api/v1/conversations/conversation/runs", json=body)
            assert duplicate.json()["id"] == run_id
            await settled(store, run_id)
            got = await client.get(f"/api/v1/runs/{run_id}")
            assert got.json()["output"] == "你好。"
            assert "owner_id" not in got.json()
            events = await client.get(
                f"/api/v1/runs/{run_id}/events", headers={"Last-Event-ID": "2"}
            )
            assert events.status_code == 200
            payloads = [
                json.loads(line[6:])
                for line in events.text.splitlines()
                if line.startswith("data: ")
            ]
            assert all(isinstance(event["created_at"], (int, float)) for event in payloads)
            ids = [int(line[4:]) for line in events.text.splitlines() if line.startswith("id: ")]
            assert ids == list(range(3, got.json()["event_seq"] + 1))
            assert (
                await client.get(
                    f"/api/v1/runs/{run_id}/events", headers={"Last-Event-ID": "invalid"}
                )
            ).status_code == 400
            assert (
                await client.get(f"/api/v1/runs/{run_id}", headers={"X-Actor": "other"})
            ).status_code == 404
            assert (
                await client.post(f"/api/v1/runs/{run_id}/cancel", headers={"X-Actor": "other"})
            ).status_code == 404
            assert (await client.post(f"/api/v1/runs/{run_id}/cancel")).json()[
                "status"
            ] == "finished"
    finally:
        await service.close()


async def test_approval_endpoint_only_records_decision_while_worker_is_stopped(store):
    writes = []

    async def write() -> str:
        writes.append(1)
        return "written"

    script = Script([call("write")], ["完成。"])
    service = AgentRunService(
        store,
        script.factory,
        {
            "write": RegisteredTool(
                tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
            )
        },
        capabilities_for_run=lambda _: frozenset({"write"}),
        poll_seconds=0.01,
    )
    await service.start()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_for(service)), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/v1/conversations/conversation/runs",
            json={"client_request_id": "approval", "prompt": "测试写入"},
        )
        run_id = created.json()["id"]
        paused = await settled(store, run_id)
        await service.close()
        approval = paused["approvals"][0]
        url = f"/api/v1/runs/{run_id}/tool-calls/call-1/approval"
        assert (
            await client.post(url, json={"fingerprint": "0" * 64, "approved": True})
        ).status_code == 409
        assert (
            await client.post(
                url,
                headers={"X-Actor": "other"},
                json={"fingerprint": approval["fingerprint"], "approved": True},
            )
        ).status_code == 404
        response = await client.post(
            url, json={"fingerprint": approval["fingerprint"], "approved": True}
        )
        assert response.status_code == 200
        assert writes == []
        assert store.get(run_id, "user")["status"] == "waiting_approval"
        await service.start()
        try:
            assert (await settled(store, run_id, status="finished"))["status"] == "finished"
            assert writes == [1]
        finally:
            await service.close()
