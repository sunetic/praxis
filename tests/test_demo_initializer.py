from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml


def _load_initializer(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("DEMO_MYSQL_APP_PASSWORD", "a" * 48)
    monkeypatch.setenv("DEMO_MYSQL_ROOT_PASSWORD", "root-demo-password")
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
    assert services["demo-init"]["restart"] == "on-failure:5"
    assert services["demo-init"]["environment"]["PRAXIS_DEMO_PORT"] == ("${PRAXIS_DEMO_PORT:-8000}")
    assert services["demo-init"]["environment"]["DEMO_MYSQL_PORT"] == ("${DEMO_MYSQL_PORT:-3308}")
    assert services["demo-init"]["environment"]["DEMO_MYSQL_ROOT_PASSWORD"] == (
        "${DEMO_MYSQL_ROOT_PASSWORD:-praxis-demo-root}"
    )
    assert services["demo-init"]["environment"]["DEMO_PROMETHEUS_PORT"] == (
        "${DEMO_PROMETHEUS_PORT:-9090}"
    )
    assert services["demo-init"]["environment"]["DEMO_EXPORTER_PORT"] == (
        "${DEMO_EXPORTER_PORT:-9104}"
    )
    assert services["praxis-demo"]["environment"]["PRAXIS_DEMO_BOOTSTRAP"] == "true"
    assert services["praxis-demo"]["environment"]["DEMO_MYSQL_APP_PASSWORD"] == (
        "${DEMO_MYSQL_APP_PASSWORD:-praxis-demo-app}"
    )
    assert set(services["demo-init"]["depends_on"]) == {
        "praxis-demo",
        "mysql-demo",
        "prometheus-demo",
        "mysql-load-demo",
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
    service = initializer.ensure_service()

    assert datasource["cluster_key"] == "mysql-prometheus-demo"
    assert datasource["host"] == "mysql-demo"
    assert datasource["password"] == "a" * 48
    assert service["service_type"] == "prometheus"
    assert service["resource_ref"] == "cluster:mysql-prometheus-demo"
    assert service["config"]["base_url"] == "http://prometheus-demo:9090"
    assert service["knowledge_base_ids"] == []
    assert len(calls) == 4


def test_initializer_creates_service_without_a_knowledge_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initializer = _load_initializer(monkeypatch)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_request(method: str, path: str, payload=None, **_kwargs):
        calls.append((method, path, payload))
        if (method, path) == ("GET", "/services"):
            return []
        if (method, path) == ("POST", "/services"):
            return {"id": 2, **payload}
        raise AssertionError(f"Unexpected request: {method} {path}")

    monkeypatch.setattr(initializer, "api_request", fake_request)

    service = initializer.ensure_service()

    assert service["knowledge_base_ids"] == []
    assert calls[-1][2]["resource_ref"] == "cluster:mysql-prometheus-demo"


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
    assert initializer.ensure_service() is service


def test_initializer_main_reports_ready_environment_without_installing_a_pack(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    initializer = _load_initializer(monkeypatch)
    monkeypatch.setattr(initializer, "wait_for_praxis", lambda: None)
    monkeypatch.setattr(initializer, "ensure_datasource", lambda: {"id": 1, "name": "Demo MySQL"})
    service_calls: list[bool] = []

    def fake_service():
        service_calls.append(True)
        return {"id": 3, "name": "Demo Prometheus"}

    monkeypatch.setattr(initializer, "ensure_service", fake_service)
    monkeypatch.setattr(initializer, "wait_for_connections", lambda *_args: None)
    monkeypatch.setattr(initializer, "verify_registration", lambda *_args: None)

    initializer.main()

    summary = capsys.readouterr().out
    assert "Praxis demo is ready." in summary
    assert "Praxis UI:        http://127.0.0.1:8000" in summary
    assert "Demo MySQL:       127.0.0.1:3308" in summary
    assert "Database:       app" in summary
    assert "Username:       app" in summary
    assert f"Password:       {'a' * 48}" in summary
    assert "Root password:  root-demo-password" in summary
    assert "Prometheus:       http://127.0.0.1:9090" in summary
    assert "MySQL Exporter:   http://127.0.0.1:9104/metrics" in summary
    assert "Registered in Praxis: Demo MySQL (ID 1), Demo Prometheus (ID 3)" in summary
    assert "Knowledge pack: not installed" in summary
    assert service_calls == [True]


def test_initializer_requires_both_objects_to_be_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    initializer = _load_initializer(monkeypatch)

    def fake_request(method: str, path: str, payload=None, **_kwargs):
        assert method == "GET"
        assert payload is None
        if path == "/datasources":
            return [{"id": 7, "name": "Demo MySQL"}]
        if path == "/services":
            return []
        raise AssertionError(path)

    monkeypatch.setattr(initializer, "api_request", fake_request)

    with pytest.raises(initializer.DemoInitError, match="service=False"):
        initializer.verify_registration(7, 8)


def test_default_compose_builds_one_source_matched_demo() -> None:
    compose_path = Path(__file__).parents[1] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text())
    services = compose["services"]

    demo_build = services["praxis-demo"]["build"]
    assert demo_build["context"] == "."
    assert demo_build["dockerfile"] == "Dockerfile"
    assert set(demo_build["args"]) == {
        "NPM_CONFIG_REGISTRY",
        "UV_INDEX_URL",
        "LITELLM_WHEEL_URL",
    }
    assert services["praxis-demo"]["image"] == "praxis-demo:local"
    assert services["demo-init"]["build"] == services["praxis-demo"]["build"]
    assert services["demo-init"]["image"] == services["praxis-demo"]["image"]
    assert services["demo-init"]["entrypoint"] == ["python", "/demo/init.py"]
    assert {
        "praxis-demo",
        "mysql-demo",
        "mysql-exporter-demo",
        "prometheus-demo",
        "mysql-load-demo",
        "demo-init",
    } <= set(services)
    assert services["demo-init"]["restart"] == "on-failure:5"


def test_compose_run_is_the_documented_one_command_entrypoint() -> None:
    root = Path(__file__).parents[1]
    command = "docker compose run --build --rm demo-init"

    assert command in (root / "README.md").read_text()
    assert command in (root / "README_CN.md").read_text()


def test_image_uses_locked_native_sdk_without_litellm_bootstrap() -> None:
    root = Path(__file__).parents[1]
    import tomllib

    packages = tomllib.loads((root / "uv.lock").read_text())["package"]
    versions = {item["name"]: item["version"] for item in packages}
    assert versions["pydantic-ai-slim"] == "2.43.0"
    assert "litellm" not in versions
    dockerfile = (root / "Dockerfile").read_text()
    assert "litellm" not in dockerfile.lower()
    assert "uv sync --frozen" in dockerfile
