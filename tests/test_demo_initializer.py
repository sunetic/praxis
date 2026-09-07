from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml


def _load_initializer(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("DEMO_MYSQL_APP_PASSWORD", "a" * 48)
    path = Path(__file__).parents[1] / "deployments" / "demo" / "init.py"
    spec = importlib.util.spec_from_file_location("praxis_demo_init", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_compose_starts_without_generated_credentials() -> None:
    compose_path = Path(__file__).parents[1] / "deployments" / "demo" / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text())
    services = compose["services"]

    mysql_environment = services["mysql-demo"]["environment"]
    assert mysql_environment["MYSQL_ROOT_PASSWORD"] == (
        "${DEMO_MYSQL_ROOT_PASSWORD:-praxis-demo-root}"
    )
    assert mysql_environment["MYSQL_PASSWORD"] == ("${DEMO_MYSQL_APP_PASSWORD:-praxis-demo-app}")
    assert mysql_environment["DEMO_EXPORTER_PASSWORD"] == (
        "${DEMO_EXPORTER_PASSWORD:-praxis-demo-exporter}"
    )
    assert services["demo-init"]["restart"] == "no"
    assert set(services["demo-init"]["depends_on"]) == {
        "praxis-demo",
        "mysql-demo",
        "prometheus-demo",
    }


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
    service = initializer.ensure_service(7)

    assert datasource["cluster_key"] == "mysql-prometheus-demo"
    assert datasource["host"] == "mysql-demo"
    assert datasource["password"] == "a" * 48
    assert service["service_type"] == "prometheus"
    assert service["resource_ref"] == "cluster:mysql-prometheus-demo"
    assert service["config"]["base_url"] == "http://prometheus-demo:9090"
    assert service["knowledge_base_ids"] == [7]
    assert len(calls) == 4


def test_initializer_reuses_existing_demo_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    initializer = _load_initializer(monkeypatch)
    datasource = {"id": 3, "cluster_key": "mysql-prometheus-demo"}
    service = {
        "id": 4,
        "service_type": "prometheus",
        "resource_ref": "cluster:mysql-prometheus-demo",
        "knowledge_base_ids": [8],
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
    assert initializer.ensure_service(8) is service


def test_initializer_links_pack_to_existing_demo_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initializer = _load_initializer(monkeypatch)
    service = {
        "id": 4,
        "service_type": "prometheus",
        "resource_ref": "cluster:mysql-prometheus-demo",
        "knowledge_base_ids": [],
    }

    def fake_request(method: str, path: str, payload=None, **_kwargs):
        if (method, path) == ("GET", "/services"):
            return [service]
        if (method, path) == ("PATCH", "/services/4"):
            return {**service, **payload}
        raise AssertionError(f"Unexpected request: {method} {path}")

    monkeypatch.setattr(initializer, "api_request", fake_request)

    assert initializer.ensure_service(8)["knowledge_base_ids"] == [8]


def test_initializer_reuses_installed_prometheus_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initializer = _load_initializer(monkeypatch)
    installed = {
        "id": "prometheus-http-api",
        "status": "installed",
        "kb_id": 9,
    }
    monkeypatch.setattr(
        initializer,
        "api_request",
        lambda *_args, **_kwargs: [installed],
    )

    assert initializer.ensure_knowledge_pack() is installed


def test_initializer_installs_available_prometheus_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initializer = _load_initializer(monkeypatch)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_request(method: str, path: str, payload=None, **_kwargs):
        calls.append((method, path, payload))
        if (method, path) == ("GET", "/knowledge-packs"):
            return [{"id": "prometheus-http-api", "status": "available"}]
        if (method, path) == ("POST", "/knowledge-packs/prometheus-http-api/install"):
            return {"status": "downloading"}
        if (method, path) == ("GET", "/knowledge-packs/prometheus-http-api/status"):
            return {"id": "prometheus-http-api", "status": "installed", "kb_id": 10}
        raise AssertionError(f"Unexpected request: {method} {path}")

    monkeypatch.setattr(initializer, "api_request", fake_request)

    assert initializer.ensure_knowledge_pack()["kb_id"] == 10
    assert calls == [
        ("GET", "/knowledge-packs", None),
        ("POST", "/knowledge-packs/prometheus-http-api/install", {}),
        ("GET", "/knowledge-packs/prometheus-http-api/status", None),
    ]


def test_initializer_main_accepts_pack_status_response_shape(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    initializer = _load_initializer(monkeypatch)
    monkeypatch.setattr(initializer, "wait_for_praxis", lambda: None)
    monkeypatch.setattr(initializer, "ensure_datasource", lambda: {"id": 1, "name": "Demo MySQL"})
    monkeypatch.setattr(
        initializer,
        "ensure_knowledge_pack",
        lambda: {"pack_id": "prometheus-http-api", "status": "installed", "kb_id": 2},
    )
    monkeypatch.setattr(
        initializer,
        "ensure_service",
        lambda knowledge_base_id: {"id": 3, "name": f"Demo Prometheus {knowledge_base_id}"},
    )
    monkeypatch.setattr(initializer, "wait_for_connections", lambda *_args: None)

    initializer.main()

    summary = json.loads(capsys.readouterr().out)
    assert summary["knowledge_pack"] == {
        "id": "prometheus-http-api",
        "status": "installed",
        "kb_id": 2,
    }
    assert summary["service"]["name"] == "Demo Prometheus 2"


def test_initializer_rejects_missing_prometheus_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    initializer = _load_initializer(monkeypatch)
    monkeypatch.setattr(initializer, "api_request", lambda *_args, **_kwargs: [])

    with pytest.raises(initializer.DemoInitError, match="knowledge pack is missing"):
        initializer.ensure_knowledge_pack()
