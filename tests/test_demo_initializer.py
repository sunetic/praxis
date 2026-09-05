from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_initializer(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("DEMO_MYSQL_APP_PASSWORD", "a" * 48)
    path = Path(__file__).parents[1] / "deployments" / "demo" / "init.py"
    spec = importlib.util.spec_from_file_location("praxis_demo_init", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_initializer_creates_cluster_bound_datasource_and_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initializer = _load_initializer(monkeypatch)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_request(method: str, path: str, payload=None, **_kwargs):
        calls.append((method, path, payload))
        if (method, path) == ("GET", "/datasources"):
            return []
        if (method, path) == ("POST", "/datasources"):
            return {"id": 1, **payload}
        if (method, path) == ("GET", "/services"):
            return []
        if (method, path) == ("POST", "/services"):
            return {"id": 2, **payload}
        raise AssertionError(f"Unexpected request: {method} {path}")

    monkeypatch.setattr(initializer, "api_request", fake_request)

    datasource = initializer.ensure_datasource()
    service = initializer.ensure_service()

    assert datasource["cluster_key"] == "mysql-prometheus-demo"
    assert datasource["host"] == "mysql-demo"
    assert datasource["password"] == "a" * 48
    assert service["service_type"] == "prometheus"
    assert service["resource_ref"] == "cluster:mysql-prometheus-demo"
    assert service["config"]["base_url"] == "http://prometheus-demo:9090"
    assert service["knowledge_base_ids"] == []
    assert len(calls) == 4


def test_initializer_reuses_existing_demo_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    initializer = _load_initializer(monkeypatch)
    datasource = {"id": 3, "cluster_key": "mysql-prometheus-demo"}
    service = {
        "id": 4,
        "service_type": "prometheus",
        "resource_ref": "cluster:mysql-prometheus-demo",
    }

    def fake_request(method: str, path: str, payload=None, **_kwargs):
        assert method == "GET"
        assert payload is None
        if path == "/datasources":
            return [datasource]
        if path == "/services":
            return [service]
        raise AssertionError(f"Unexpected request: {method} {path}")

    monkeypatch.setattr(initializer, "api_request", fake_request)

    assert initializer.ensure_datasource() is datasource
    assert initializer.ensure_service() is service


@pytest.mark.parametrize("status", ["available", "installed"])
def test_initializer_accepts_downloadable_or_installed_prometheus_pack(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    initializer = _load_initializer(monkeypatch)
    monkeypatch.setattr(
        initializer,
        "api_request",
        lambda *_args, **_kwargs: [
            {"id": "prometheus-http-api", "status": status},
        ],
    )

    assert initializer.verify_knowledge_pack()["status"] == status


def test_initializer_rejects_missing_prometheus_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    initializer = _load_initializer(monkeypatch)
    monkeypatch.setattr(initializer, "api_request", lambda *_args, **_kwargs: [])

    with pytest.raises(initializer.DemoInitError, match="knowledge pack is missing"):
        initializer.verify_knowledge_pack()
