"""Read-only Agent SQL routes through paired cluster credentials."""

import asyncio
import json

from test_custom_agents import create, custom_app  # noqa: F401
from test_runtime import Script, call

from app.models.models import DataSource
from app.services.datasource import agent_tools


async def _finish(runtime, run_id):
    async with asyncio.timeout(5):
        while True:
            row = runtime.store.get(run_id, "local")
            if row["status"] not in {"queued", "running"}:
                assert row["status"] == "finished", row
                return row
            await asyncio.sleep(0.01)


async def test_system_schema_query_routes_from_selected_user_to_cluster_admin(
    custom_app, monkeypatch  # noqa: F811
):
    runtime, client = custom_app
    with runtime.sessions.begin() as db:
        user = db.get(DataSource, 1)
        user.host = "mysql-demo"
        user.port = 3306
        user.db_type = "mysql"
        user.cluster_key = "demo-cluster"
        user.access_level = "user"
        user.tenant_role = "user"
        user.user = "app"
        user.password = "app-password"
        user.database = "app"
        db.add(
            DataSource(
                id=4,
                name="demo-admin",
                host="mysql-demo",
                port=3306,
                db_type="mysql",
                cluster_key="demo-cluster",
                access_level="admin",
                tenant_role="sys",
                user="root",
                password="root-password",
                database="app",
                status="active",
            )
        )

    observed = []

    class Pool:
        async def execute_read_query(self, datasource, sql, *, limit):
            observed.append((datasource.id, datasource.user, sql, limit))
            return {
                "columns": ["ENABLED"],
                "rows": [{"ENABLED": "YES"}],
                "returned_rows": 1,
                "truncated": False,
                "total_rows": 1,
            }

    monkeypatch.setattr(agent_tools, "get_pool_for_datasource", lambda _datasource: Pool())
    agent = await create(client, tools=["list_datasources", "query_database"])
    conversation = (
        await client.post("/api/v1/conversations", json={"scene": {"agent_id": agent["id"]}})
    ).json()["id"]
    script = Script(
        [
            call(
                "query_database",
                json.dumps(
                    {
                        "datasource_id": 1,
                        "access_level": "admin",
                        "sql": "SELECT ENABLED FROM performance_schema.setup_consumers",
                    }
                ),
            )
        ],
        ["The system-schema query used the paired admin credential."],
    )
    runtime.service.model_factory = script.factory

    response = await client.post(
        f"/api/v1/conversations/{conversation}/runs",
        json={"client_request_id": "admin-route", "prompt": "Inspect Performance Schema."},
    )
    assert response.status_code == 202, response.text
    row = await _finish(runtime, response.json()["id"])

    assert row["definition"]["scope"]["datasource_ids"] == [1]
    assert observed == [(4, "root", "SELECT ENABLED FROM performance_schema.setup_consumers", 200)]
    tool_call = row["tool_calls"][0]
    assert tool_call["target"]["requested_datasource_id"] == 1
    assert tool_call["target"]["datasource_id"] == 4
    assert tool_call["target"]["resolved_access_level"] == "admin"
    assert tool_call["result"]["content"]["routing_reason"] == "resolved_admin_by_cluster_key"
