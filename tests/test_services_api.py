from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.security import is_encrypted
from app.db.database import Base, get_db
from app.models import models
from app.services.integration.bindings import list_bound_services


@pytest.fixture()
def service_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.api import services as services_api
    from app.main import app

    engine = create_engine(f"sqlite:///{tmp_path / 'services.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)

    def override_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    call = AsyncMock(return_value={"http_status": 200, "content_type": "text/plain", "data": "ready"})
    monkeypatch.setattr(services_api, "call_http_service", call)
    try:
        yield TestClient(app), factory, engine, call
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _payload(kb_id: int) -> dict:
    return {
        "name": "Demo Prometheus",
        "service_type": "prometheus",
        "resource_ref": "cluster:mysql-prometheus-demo",
        "knowledge_base_ids": [kb_id],
        "config": {
            "base_url": "http://prometheus-demo:9090",
            "auth_type": "bearer",
            "health_check_path": "/-/ready",
            "response_format": "auto",
        },
        "secrets": {"bearer_token": "top-secret"},
    }


def test_service_crud_encrypts_credentials_and_returns_linked_knowledge(service_api) -> None:
    client, factory, engine, _ = service_api
    with factory() as db:
        kb = models.KnowledgeBase(name="Prometheus HTTP API", source="pack")
        db.add(kb)
        db.commit()
        db.refresh(kb)
        kb_id = kb.id

    response = client.post("/api/v1/services", json=_payload(kb_id))
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["config"]["base_url"] == "http://prometheus-demo:9090"
    assert created["has_credentials"] is True
    assert created["knowledge_base_ids"] == [kb_id]
    assert "secrets" not in created

    with engine.connect() as connection:
        raw = connection.execute(
            text("SELECT secrets FROM services WHERE id = :id"), {"id": created["id"]}
        ).scalar_one()
    assert is_encrypted(raw)
    assert "top-secret" not in raw

    listed = client.get("/api/v1/services").json()
    assert listed[0]["id"] == created["id"]
    assert listed[0]["knowledge_base_ids"] == [kb_id]

    updated = client.patch(
        f"/api/v1/services/{created['id']}",
        json={"name": "Renamed", "secrets": {"bearer_token": ""}},
    )
    assert updated.status_code == 200
    with factory() as db:
        service = db.get(models.Service, created["id"])
        assert service is not None
        assert service.secrets["bearer_token"] == "top-secret"


def test_service_connection_test_uses_configured_health_path(service_api) -> None:
    client, factory, _, call = service_api
    with factory() as db:
        kb = models.KnowledgeBase(name="Prometheus HTTP API")
        db.add(kb)
        db.commit()
        db.refresh(kb)
        kb_id = kb.id
    created = client.post("/api/v1/services", json=_payload(kb_id)).json()

    response = client.post(f"/api/v1/services/{created['id']}/test")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": "Connection successful",
        "http_status": 200,
    }
    assert call.await_args.kwargs["path"] == "/-/ready"
    assert call.await_args.kwargs["raw_secrets"]["bearer_token"] == "top-secret"

    edited = client.post(
        f"/api/v1/services/{created['id']}/test-config",
        json={
            "config": {
                "base_url": "http://prometheus-preview:9090",
                "auth_type": "bearer",
                "health_check_path": "/preview-ready",
            },
            "secrets": {"bearer_token": "replacement-token"},
        },
    )
    assert edited.status_code == 200
    assert call.await_args.kwargs["raw_config"]["base_url"] == (
        "http://prometheus-preview:9090"
    )
    assert call.await_args.kwargs["path"] == "/preview-ready"
    assert call.await_args.kwargs["raw_secrets"]["bearer_token"] == (
        "replacement-token"
    )


def test_service_rejects_invalid_url_and_missing_knowledge_base(service_api) -> None:
    client, _, _, _ = service_api
    invalid_url = _payload(9)
    invalid_url["config"]["base_url"] = "file:///etc/passwd"
    assert client.post("/api/v1/services", json=invalid_url).status_code == 422

    exposed_secret = _payload(9)
    exposed_secret["config"]["default_headers"] = {"Authorization": "secret"}
    assert client.post("/api/v1/services", json=exposed_secret).status_code == 422

    missing_kb = _payload(999)
    response = client.post("/api/v1/services", json=missing_kb)
    assert response.status_code == 422
    assert "Knowledge bases not found" in response.json()["detail"]


def test_service_binding_supports_cluster_and_datasource_refs(service_api) -> None:
    _, factory, _, _ = service_api
    with factory() as db:
        datasource = models.DataSource(
            name="customer database",
            host="mysql.internal",
            port=3306,
            db_type="mysql",
            cluster_key="cluster-a",
            tenant_role="user",
            user="app",
            password="test-password",
            database="app",
        )
        db.add(datasource)
        db.flush()
        db.add_all(
            [
                models.Service(
                    name="cluster metrics",
                    service_type="prometheus",
                    config={"base_url": "http://prometheus:9090"},
                    resource_ref="cluster:cluster-a",
                ),
                models.Service(
                    name="direct alerts",
                    service_type="alertmanager",
                    config={"base_url": "http://alertmanager:9093"},
                    resource_ref=f"datasource:{datasource.id}",
                ),
            ]
        )
        db.commit()
        services = list_bound_services(db, datasource)

    assert [item.name for item in services] == ["cluster metrics", "direct alerts"]
