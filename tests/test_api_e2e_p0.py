"""Cross-domain CRUD checks; native execution contracts live in tests/agent_runtime."""

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import agents, channels, datasources, onboarding, settings, skills
from app.api.agent_runs import create_run_router
from app.core.security import is_encrypted
from app.db.database import Base, get_db
from app.models import models
from app.services.agent.application import RuntimeApplication, local_actor


def _datasource_payload(
    name: str,
    *,
    cluster_key: str = "127.0.0.1:2881",
) -> dict[str, Any]:
    return {
        "name": name,
        "host": "127.0.0.1",
        "port": 2881,
        "db_type": "oceanbase",
        "cluster_key": cluster_key,
        "tenant_role": "user",
        "user": "tester",
        "password": "secret",
        "database": "test",
    }


def _create_datasource(client: TestClient, name: str) -> dict[str, Any]:
    response = client.post("/api/v1/datasources", json=_datasource_payload(name))
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'api.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    runtime = RuntimeApplication(sessions=sessions)
    app = FastAPI()
    app.state.agent_runtime = runtime
    for module in (agents, channels, datasources, onboarding, settings, skills):
        app.include_router(module.router, prefix="/api/v1")
    app.include_router(
        create_run_router(
            runtime.service, actor_dependency=local_actor, resolve_definition=runtime.resolve
        )
    )

    def session():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    monkeypatch.setattr("app.api.datasources._probe_and_fill_ob_ids", None)
    from app.core.config import get_settings
    from app.services.platform.object_tools import ObjectToolService

    monkeypatch.setattr(get_settings(), "scheduler_autostart", False)
    monkeypatch.setattr(channels, "_object_service", ObjectToolService(session_factory=sessions))
    with TestClient(app) as client:
        yield client, sessions
    engine.dispose()


def test_p0_datasource_test_and_agent_reference(api_client, monkeypatch: pytest.MonkeyPatch):
    client, _ = api_client

    class _FakePool:
        async def test_connection(  # noqa: ARG002
            self, host, port, user, password, database, db_type="mysql"
        ):
            return True, "ok"

    monkeypatch.setattr("app.db.connection.get_db_pool", lambda: _FakePool())

    ds = _create_datasource(client, "ds-e2e-main")
    tested = client.post(f"/api/v1/datasources/{ds['id']}/test")
    assert tested.status_code == 200, tested.text
    assert tested.json()["success"] is True

    agent = client.post(
        "/api/v1/agents",
        json={
            "name": "ds-bind-agent",
            "prompt": "agent prompt",
            "tools": [],
            "skills": [],
            "agent_type": "custom",
            "datasource_ids": [ds["id"]],
        },
    )
    assert agent.status_code == 201, agent.text
    opened = client.post(
        "/api/v1/conversations",
        json={"scene": {"agent_id": agent.json()["id"], "datasource_ids": [ds["id"]]}},
    )
    assert opened.status_code == 201, opened.text
    assert opened.json()["scene"]["datasource_ids"] == [ds["id"]]


# ---------------------------------------------------------------------------
# Cross-domain smoke tests — verify every API domain is reachable and returns
# expected status codes after the modular restructure.
# ---------------------------------------------------------------------------


def test_p0_datasource_crud_lifecycle(api_client):
    client, _ = api_client

    listed = client.get("/api/v1/datasources")
    assert listed.status_code == 200
    assert isinstance(listed.json(), list)

    ds = _create_datasource(client, "ds-crud-smoke")
    ds_id = ds["id"]
    assert ds["name"] == "ds-crud-smoke"
    assert "password" not in ds

    listed_after_create = client.get("/api/v1/datasources")
    assert listed_after_create.status_code == 200
    assert listed_after_create.json()[0]["id"] == ds_id
    assert "password" not in listed_after_create.json()[0]

    fetched = client.get(f"/api/v1/datasources/{ds_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == ds_id
    assert "password" not in fetched.json()

    updated = client.patch(f"/api/v1/datasources/{ds_id}", json={"name": "ds-crud-renamed"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "ds-crud-renamed"
    assert "password" not in updated.json()

    connect_info = client.get(f"/api/v1/datasources/{ds_id}/connect-info")
    assert connect_info.status_code == 410
    assert "secret" not in connect_info.text

    deleted = client.delete(f"/api/v1/datasources/{ds_id}")
    assert deleted.status_code in (200, 204)

    gone = client.get(f"/api/v1/datasources/{ds_id}")
    assert gone.status_code == 404


def test_p0_datasource_cluster_access_level_invariants(api_client):
    client, _ = api_client
    base_payload = {
        "host": "127.0.0.1",
        "port": 3307,
        "db_type": "mysql",
        "cluster_key": "mysql-access-pair",
        "tenant_role": "user",
        "user": "tester",
        "password": "secret",
        "database": "app",
    }

    user = client.post(
        "/api/v1/datasources",
        json={**base_payload, "name": "mysql-user", "access_level": "user"},
    )
    assert user.status_code == 201, user.text
    assert user.json()["access_level"] == "user"

    admin = client.post(
        "/api/v1/datasources",
        json={**base_payload, "name": "mysql-admin", "access_level": "admin"},
    )
    assert admin.status_code == 201, admin.text
    assert admin.json()["access_level"] == "admin"

    duplicate_admin = client.post(
        "/api/v1/datasources",
        json={**base_payload, "name": "mysql-admin-2", "access_level": "admin"},
    )
    assert duplicate_admin.status_code == 400
    assert "already has an active admin datasource" in duplicate_admin.json()["detail"]

    mixed_engine = client.post(
        "/api/v1/datasources",
        json={
            **base_payload,
            "name": "postgres-user",
            "db_type": "postgresql",
            "port": 5432,
            "access_level": "user",
        },
    )
    assert mixed_engine.status_code == 400
    assert "already associated with database type" in mixed_engine.json()["detail"]


def test_p0_settings_get_redacts_api_key(api_client):
    client, session_local = api_client

    updated = client.patch("/api/v1/settings", json={"ai_api_key": "secret-value"})
    assert updated.status_code == 200
    assert updated.json()["ai_api_key_configured"] is True
    assert "ai_api_key" not in updated.json()
    with session_local() as db:
        stored_api_key = db.get(models.PlatformSetting, "ai_api_key").value
        assert isinstance(stored_api_key, str)
        assert is_encrypted(stored_api_key)

    resp = client.get("/api/v1/settings")
    assert resp.status_code == 200
    payload = resp.json()
    assert isinstance(payload, dict)
    assert payload["ai_api_key_configured"] is True
    assert "ai_api_key" not in payload


def test_p0_onboarding_status_and_complete(api_client):
    client, session_local = api_client

    status = client.get("/api/v1/onboarding/status")
    assert status.status_code == 200
    assert "completed" in status.json()

    complete = client.post(
        "/api/v1/onboarding/complete",
        json={
            "llm_config": {
                "llm_provider": "test",
                "llm_api_key": "sk-test",
                "llm_model": "gpt-test",
            }
        },
    )
    assert complete.status_code == 200
    assert complete.json()["completed"] is True
    with session_local() as db:
        stored_api_key = db.get(models.PlatformSetting, "ai_api_key").value
        assert isinstance(stored_api_key, str)
        assert is_encrypted(stored_api_key)

    status_after = client.get("/api/v1/onboarding/status")
    assert status_after.json()["completed"] is True


def test_p0_skills_list(api_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client, _ = api_client
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "test_skill.yaml").write_text("name: test_skill\nprompt: hello\nsource: custom\n")
    from app.api import skills as skills_api
    from app.skills.store import SkillStore

    monkeypatch.setattr(skills_api, "skill_store", SkillStore(skills_dir=str(skills_dir)))

    resp = client.get("/api/v1/skills")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_p0_channels_list(api_client):
    client, _ = api_client
    resp = client.get("/api/v1/channels")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_p0_unknown_api_is_not_handled_by_spa_fallback(api_client):
    client, _ = api_client

    resp = client.get("/api/v1/removed-feature")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
