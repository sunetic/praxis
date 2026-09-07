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
CLUSTER_KEY = "mysql-prometheus-demo"
PROMETHEUS_PACK_ID = "prometheus-http-api"
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


def ensure_service(knowledge_base_id: int) -> dict[str, Any]:
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
        knowledge_base_ids = list(existing.get("knowledge_base_ids") or [])
        if knowledge_base_id not in knowledge_base_ids:
            knowledge_base_ids.append(knowledge_base_id)
            return api_request(
                "PATCH",
                f"/services/{existing['id']}",
                {"knowledge_base_ids": knowledge_base_ids},
            )
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
            "knowledge_base_ids": [knowledge_base_id],
        },
    )


def wait_for_connections(datasource_id: int, service_id: int, timeout_seconds: int = 120) -> None:
    """Verify both preconfigured integrations before reporting success."""
    deadline = time.monotonic() + timeout_seconds
    last_results: tuple[Any, Any] = (None, None)
    while time.monotonic() < deadline:
        database = api_request("POST", f"/datasources/{datasource_id}/test", {})
        service = api_request("POST", f"/services/{service_id}/test", {})
        last_results = (database, service)
        if database.get("success") and service.get("success"):
            return
        time.sleep(3)
    raise DemoInitError(f"Demo connections did not become ready: {last_results!r}")


def ensure_knowledge_pack(timeout_seconds: int = 120) -> dict[str, Any]:
    """Install the bundled Prometheus pack and return its knowledge-base ID."""
    packs = api_request("GET", "/knowledge-packs")
    pack = next((item for item in packs if item.get("id") == PROMETHEUS_PACK_ID), None)
    if not pack:
        raise DemoInitError("Bundled Prometheus knowledge pack is missing")
    if pack.get("status") == "installed" and pack.get("kb_id"):
        return pack
    if pack.get("status") == "available":
        api_request("POST", f"/knowledge-packs/{PROMETHEUS_PACK_ID}/install", {})

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = api_request("GET", f"/knowledge-packs/{PROMETHEUS_PACK_ID}/status")
        if status.get("status") == "installed" and status.get("kb_id"):
            return status
        if status.get("status") == "error":
            raise DemoInitError(
                "Prometheus knowledge pack installation failed: "
                f"{status.get('error_message') or 'unknown error'}"
            )
        time.sleep(0.5)
    raise DemoInitError("Prometheus knowledge pack installation timed out")


def main() -> None:
    """Initialize and verify all first-run demo objects."""
    wait_for_praxis()
    datasource = ensure_datasource()
    pack = ensure_knowledge_pack()
    service = ensure_service(int(pack["kb_id"]))
    wait_for_connections(int(datasource["id"]), int(service["id"]))
    print(
        json.dumps(
            {
                "datasource": {"id": datasource["id"], "name": datasource["name"]},
                "service": {"id": service["id"], "name": service["name"]},
                "knowledge_pack": {
                    "id": PROMETHEUS_PACK_ID,
                    "status": pack["status"],
                    "kb_id": pack["kb_id"],
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
