"""Idempotent first-run objects for the explicitly enabled Compose demo."""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import models

DEMO_CLUSTER_KEY = "mysql-prometheus-demo"
DEMO_RESOURCE_REF = f"cluster:{DEMO_CLUSTER_KEY}"


@dataclass(frozen=True)
class DemoBootstrapResult:
    datasource_id: int
    service_id: int
    datasource_created: bool
    service_created: bool


def demo_bootstrap_enabled() -> bool:
    """Return whether the process was explicitly started as the local demo."""
    return os.getenv("PRAXIS_DEMO_BOOTSTRAP", "").strip().lower() in {"1", "true", "yes", "on"}


def bootstrap_demo_integrations(db: Session) -> DemoBootstrapResult | None:
    """Ensure the demo datasource and service exist in this process's database."""
    if not demo_bootstrap_enabled():
        return None

    mysql_password = os.environ["DEMO_MYSQL_APP_PASSWORD"]
    datasource = (
        db.query(models.DataSource)
        .filter(models.DataSource.cluster_key == DEMO_CLUSTER_KEY)
        .order_by(models.DataSource.id.asc())
        .first()
    )
    datasource_created = datasource is None
    if datasource is None:
        datasource = models.DataSource(
            name="Demo MySQL",
            host="mysql-demo",
            port=3306,
            db_type="mysql",
            cluster_key=DEMO_CLUSTER_KEY,
            access_level="user",
            tenant_role="user",
            user="app",
            password=mysql_password,
            database="app",
            status="active",
        )
        db.add(datasource)
        db.flush()

    service = (
        db.query(models.Service)
        .filter(
            models.Service.service_type == "prometheus",
            models.Service.resource_ref == DEMO_RESOURCE_REF,
        )
        .order_by(models.Service.id.asc())
        .first()
    )
    service_created = service is None
    if service is None:
        service = models.Service(
            name="Demo Prometheus",
            service_type="prometheus",
            config={
                "base_url": "http://prometheus-demo:9090",
                "auth_type": "none",
                "api_key_header": "X-API-Key",
                "default_headers": {},
                "health_check_path": "/-/ready",
                "health_check_method": "GET",
                "response_format": "auto",
                "timeout_seconds": 30,
                "verify_tls": True,
                "use_environment_proxy": False,
                "max_response_bytes": 262144,
            },
            resource_ref=DEMO_RESOURCE_REF,
            status="active",
        )
        db.add(service)
        db.flush()

    db.commit()
    return DemoBootstrapResult(
        datasource_id=datasource.id,
        service_id=service.id,
        datasource_created=datasource_created,
        service_created=service_created,
    )
