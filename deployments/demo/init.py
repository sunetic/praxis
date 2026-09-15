"""Initialize the first-run Praxis demo through its public HTTP API."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

API_URL = os.getenv("PRAXIS_API_URL", "http://praxis-demo:8000/api/v1").rstrip("/")
MYSQL_PASSWORD = os.environ["DEMO_MYSQL_APP_PASSWORD"]
MYSQL_ROOT_PASSWORD = os.environ["DEMO_MYSQL_ROOT_PASSWORD"]
CLUSTER_KEY = "mysql-prometheus-demo"
OPENER = build_opener(ProxyHandler({}))


class DemoInitError(RuntimeError):
    """Raised when the demo cannot be initialized."""


def api_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 10,
) -> Any:
    """Call one Praxis API endpoint and decode its JSON response."""
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        f"{API_URL}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with OPENER.open(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read().decode()
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise DemoInitError(f"{method} {path} returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise DemoInitError(f"{method} {path} failed: {exc.reason}") from exc
    return json.loads(raw) if raw else None


def wait_for_praxis(timeout_seconds: int = 180) -> None:
    """Wait until the API has completed migrations and bootstrap."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            api_request("GET", "/onboarding/status", timeout=3)
            return
        except DemoInitError:
            time.sleep(2)
    raise DemoInitError("Praxis API did not become ready within 180 seconds")


def retry_step(
    label: str,
    operation: Any,
    *,
    timeout_seconds: int = 180,
) -> Any:
    """Retry one idempotent initialization step across transient startup failures."""
    deadline = time.monotonic() + timeout_seconds
    last_error: DemoInitError | None = None
    while time.monotonic() < deadline:
        try:
            return operation()
        except DemoInitError as exc:
            last_error = exc
            time.sleep(2)
    raise DemoInitError(f"{label} did not complete: {last_error}") from last_error


def ensure_datasource() -> dict[str, Any]:
    """Create the demo MySQL datasource once."""
    datasources = api_request("GET", "/datasources")
    existing = next(
        (item for item in datasources if item.get("cluster_key") == CLUSTER_KEY),
        None,
    )
    if existing:
        return existing
    return api_request(
        "POST",
        "/datasources",
        {
            "name": "Demo MySQL",
            "host": "mysql-demo",
            "port": 3306,
            "db_type": "mysql",
            "cluster_key": CLUSTER_KEY,
            "access_level": "user",
            "tenant_role": "user",
            "user": "app",
            "password": MYSQL_PASSWORD,
            "database": "app",
        },
    )


def ensure_service() -> dict[str, Any]:
    """Create the cluster-bound Prometheus Service once."""
    services = api_request("GET", "/services")
    existing = next(
        (
            item
            for item in services
            if item.get("service_type") == "prometheus"
            and item.get("resource_ref") == f"cluster:{CLUSTER_KEY}"
        ),
        None,
    )
    if existing:
        return existing
    return api_request(
        "POST",
        "/services",
        {
            "name": "Demo Prometheus",
            "service_type": "prometheus",
            "config": {
                "base_url": "http://prometheus-demo:9090",
                "auth_type": "none",
                "health_check_path": "/-/ready",
                "health_check_method": "GET",
                "response_format": "auto",
                "timeout_seconds": 30,
                "verify_tls": True,
                "use_environment_proxy": False,
                "max_response_bytes": 262144,
            },
            "resource_ref": f"cluster:{CLUSTER_KEY}",
            "knowledge_base_ids": [],
        },
    )


def wait_for_connections(datasource_id: int, service_id: int, timeout_seconds: int = 120) -> None:
    """Verify both preconfigured integrations before reporting success."""
    deadline = time.monotonic() + timeout_seconds
    next_progress = 0.0
    last_results: tuple[Any, Any] = (None, None)
    while time.monotonic() < deadline:
        database = api_request("POST", f"/datasources/{datasource_id}/test", {})
        service = api_request("POST", f"/services/{service_id}/test", {})
        last_results = (database, service)
        if database.get("success") and service.get("success"):
            return
        now = time.monotonic()
        if now >= next_progress:
            print(
                "  Still waiting: "
                f"MySQL={database.get('message', 'unknown')}; "
                f"Prometheus={service.get('message', 'unknown')}",
                flush=True,
            )
            next_progress = now + 10
        time.sleep(3)
    raise DemoInitError(f"Demo connections did not become ready: {last_results!r}")


def verify_registration(datasource_id: int, service_id: int) -> None:
    """Confirm fresh list calls expose both objects to the same API used by the UI."""
    datasources = api_request("GET", "/datasources")
    services = api_request("GET", "/services")
    datasource_visible = any(
        int(item.get("id", -1)) == datasource_id and item.get("name") == "Demo MySQL"
        for item in datasources
    )
    service_visible = any(
        int(item.get("id", -1)) == service_id and item.get("name") == "Demo Prometheus"
        for item in services
    )
    if not datasource_visible or not service_visible:
        raise DemoInitError(
            "Demo objects were not visible through list APIs: "
            f"datasource={datasource_visible}, service={service_visible}"
        )


def main() -> None:
    """Initialize and verify all first-run demo objects."""
    print("[1/4] Waiting for the Praxis API...", flush=True)
    wait_for_praxis()
    print("[2/4] Registering Demo MySQL and Demo Prometheus...", flush=True)
    datasource = retry_step("Demo MySQL registration", ensure_datasource)
    service = retry_step("Demo Prometheus registration", ensure_service)
    print("[3/4] Verifying both connections...", flush=True)
    wait_for_connections(int(datasource["id"]), int(service["id"]))
    print("[4/4] Confirming both objects are visible in Praxis...", flush=True)
    verify_registration(int(datasource["id"]), int(service["id"]))
    print(
        f"""
Praxis demo is ready.

  Praxis UI:        http://127.0.0.1:{int(os.getenv("PRAXIS_DEMO_PORT", "8000"))}
  Demo MySQL:       127.0.0.1:{int(os.getenv("DEMO_MYSQL_PORT", "3308"))}
    Database:       app
    Username:       app
    Password:       {MYSQL_PASSWORD}
    Root password:  {MYSQL_ROOT_PASSWORD}
  Prometheus:       http://127.0.0.1:{int(os.getenv("DEMO_PROMETHEUS_PORT", "9090"))}
  MySQL Exporter:   http://127.0.0.1:{int(os.getenv("DEMO_EXPORTER_PORT", "9104"))}/metrics

Registered in Praxis: {datasource["name"]} (ID {datasource["id"]}), {service["name"]} (ID {service["id"]})
Knowledge pack: not installed; download it from Knowledge Packs when needed.
""".strip()
    )


if __name__ == "__main__":
    main()
