"""Native service authorization and durable dispatch; scripted models are not quality scores."""

import asyncio
import json

import httpx
import pytest
from test_custom_agents import create, custom_app  # noqa: F401
from test_runtime import Script, call

from app.models.models import Agent, DataSource, Service
from app.services.integration import agent_tools
from app.services.integration.http_service import call_http_service


@pytest.fixture
async def services(custom_app):  # noqa: F811
    runtime, client = custom_app
    with runtime.sessions.begin() as db:
        db.get(DataSource, 1).cluster_key = "cluster-one"
        db.add_all(
            [
                Service(
                    id=id,
                    name=f"service-{id}",
                    service_type="http_api",
                    resource_ref=ref,
                    status=status,
                    config={"base_url": "http://fixture.invalid/api", "auth_type": "bearer"},
                    secrets={"bearer_token": "private-service-credential"},
                )
                for id, ref, status in [
                    (1, "datasource:1", "active"),
                    (2, "cluster:cluster-one", "active"),
                    (3, "datasource:2", "active"),
                    (4, "datasource:1", "inactive"),
                    (5, None, "active"),
                ]
            ]
        )
    agent = await create(client, tools=["list_services", "call_service"])
    conversation = (
        await client.post(
            "/api/v1/conversations",
            json={
                "scene": {"agent_id": agent["id"]},
            },
        )
    ).json()["id"]
    yield runtime, client, agent, conversation


async def wait(runtime, id, status):
    async with asyncio.timeout(5):
        while True:
            row = runtime.store.get(id, "local")
            if row["status"] == status:
                return row
            assert row["status"] in {"queued", "running", "waiting_approval"}, row
            await asyncio.sleep(0.01)


async def submit(services, script):
    runtime, client, _, conversation = services
    runtime.service.model_factory = script.factory
    response = await client.post(
        f"/api/v1/conversations/{conversation}/runs",
        json={
            "client_request_id": "test",
            "prompt": "Use the authorized test service.",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


async def approve(services, row, approved=True):
    _, client, _, _ = services
    pending = row["approvals"][0]
    response = await client.post(
        f"/api/v1/runs/{row['id']}/tool-calls/{pending['call_id']}/approval",
        json={"fingerprint": pending["fingerprint"], "approved": approved},
    )
    assert response.status_code == 200, response.text


def transport(monkeypatch, handler):
    def client_factory(**kwargs):
        kwargs.pop("verify", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    async def invoke(**kwargs):
        return await call_http_service(**kwargs, client_factory=client_factory)

    monkeypatch.setattr(agent_tools, "call_http_service", invoke)


async def test_catalog_is_bound_to_current_grants_and_explicit_scene(services):
    runtime, client, agent, conversation = services
    assert runtime.resolve(conversation, "local", {}).scope["service_ids"] == [1, 2]
    assert runtime.resolve(conversation, "local", {"service_ids": []}).scope["service_ids"] == []
    for ids in ([3], [4], [5], [999]):
        response = await client.post(
            "/api/v1/conversations",
            json={
                "scene": {"agent_id": agent["id"], "service_ids": ids},
            },
        )
        assert response.status_code == 422
    run_id = await submit(services, Script([call("list_services")], ["Only bound services."]))
    row = await wait(runtime, run_id, "finished")
    result = row["tool_calls"][0]["result"]["content"]
    assert [item["id"] for item in result["items"]] == [1, 2]
    assert "private-service-credential" not in json.dumps(row)


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_exact_approval_executes_once_and_credentials_never_enter_history(
    services, monkeypatch, method
):
    runtime, _, _, _ = services
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer private-service-credential"
        return httpx.Response(
            200, json={"authorization": request.headers["authorization"], "value": 17}
        )

    transport(monkeypatch, handler)
    run_id = await submit(
        services,
        Script(
            [
                call(
                    "call_service",
                    json.dumps(
                        {
                            "service_id": 1,
                            "method": method,
                            "path": "/items",
                            "body": {"label": "test"} if method == "POST" else None,
                        }
                    ),
                )
            ],
            ["The response is available."],
        ),
    )
    row = await wait(runtime, run_id, "waiting_approval")
    assert not requests
    await approve(services, row)
    await approve(services, row)
    row = await wait(runtime, run_id, "finished")
    assert len(requests) == 1
    assert row["tool_calls"][0]["status"] == "succeeded"
    assert row["tool_calls"][0]["result"]["content"]["data"]["value"] == 17
    receipt = row["tool_calls"][0]["result"]["content"]["approval"]
    assert receipt["decision"] == "approved" and receipt["decided_by"] == "local"
    assert receipt["call_id"] == row["tool_calls"][0]["call_id"]
    assert "private-service-credential" not in json.dumps(row)
    assert "private-service-credential" not in repr(runtime.store.history(run_id, "local"))


@pytest.mark.parametrize(
    "change", ["revoke", "binding", "inactive", "credentials", "config", "deny"]
)
async def test_changed_or_denied_authorization_never_sends_http(services, monkeypatch, change):
    runtime, _, agent, _ = services
    requests = []
    transport(monkeypatch, lambda request: requests.append(request) or httpx.Response(200, json={}))
    run_id = await submit(
        services,
        Script(
            [
                call(
                    "call_service",
                    json.dumps(
                        {
                            "service_id": 1,
                            "method": "POST",
                            "path": "/items",
                        }
                    ),
                )
            ],
            ["No action was sent."],
        ),
    )
    row = await wait(runtime, run_id, "waiting_approval")
    with runtime.sessions.begin() as db:
        service = db.get(Service, 1)
        if change == "revoke":
            db.get(Agent, agent["id"]).datasources = []
        elif change == "binding":
            service.resource_ref = "datasource:2"
        elif change == "inactive":
            service.status = "inactive"
        elif change == "credentials":
            service.secrets = {"bearer_token": "new-private-credential"}
        elif change == "config":
            service.config = {**service.config, "base_url": "http://other.invalid"}
    await approve(services, row, approved=change != "deny")
    await wait(runtime, run_id, "finished")
    assert not requests


@pytest.mark.parametrize("failure", ["read_timeout", "server_error"])
async def test_uncertain_external_effect_is_not_replayed(services, monkeypatch, failure):
    runtime, _, _, _ = services
    requests = []

    def handler(request):
        requests.append(request)
        if failure == "read_timeout":
            raise httpx.ReadTimeout("private-service-credential", request=request)
        return httpx.Response(500, json={"error": "response lost after commit"})

    transport(monkeypatch, handler)
    script = Script([call("call_service", '{"service_id":1,"method":"POST","path":"/items"}')])
    run_id = await submit(services, script)
    row = await wait(runtime, run_id, "waiting_approval")
    await approve(services, row)
    row = await wait(runtime, run_id, "interrupted")
    assert row["tool_calls"][0]["status"] == "outcome_unknown"
    diagnostics = row["tool_calls"][0]["result"]["diagnostics"]
    assert diagnostics["code"] == ("timeout" if failure == "read_timeout" else "api_error")
    if failure == "server_error":
        assert diagnostics["http_status"] == 500
    assert len(requests) == 1 and len(script.requests) == 1
    assert "private-service-credential" not in json.dumps(row)


async def test_http_business_failure_returns_to_same_agent(services, monkeypatch):
    runtime, _, _, _ = services
    transport(
        monkeypatch, lambda request: httpx.Response(422, json={"error": "missing field label"})
    )
    script = Script(
        [call("call_service", '{"service_id":1,"method":"POST","path":"/items"}')],
        ["The service rejected the missing label."],
    )
    run_id = await submit(services, script)
    row = await wait(runtime, run_id, "waiting_approval")
    await approve(services, row)
    row = await wait(runtime, run_id, "finished")
    assert row["tool_calls"][0]["status"] == "failed"
    assert "missing field label" in repr(script.requests[-1][0])


async def test_ignored_body_cannot_reopen_a_denied_delete(services, monkeypatch):
    runtime, _, _, _ = services
    requests = []
    transport(monkeypatch, lambda request: requests.append(request) or httpx.Response(200))
    original = {"service_id": 1, "method": "DELETE", "path": "/notes/one"}
    script = Script(
        [call("call_service", json.dumps(original), "first")],
        [call("call_service", json.dumps({**original, "body": {"id": "one"}}), "invalid")],
        [call("call_service", json.dumps(original), "repeated")],
        ["The deletion was denied; nothing was sent."],
    )
    run_id = await submit(services, script)
    row = await wait(runtime, run_id, "waiting_approval")
    await approve(services, row, approved=False)
    row = await wait(runtime, run_id, "finished")
    assert len(row["approvals"]) == 1
    assert not requests
    assert "only for POST" in repr(script.requests[2][0])
    assert all(item["status"] == "denied" for item in row["tool_calls"])
