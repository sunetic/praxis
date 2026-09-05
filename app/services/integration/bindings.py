"""Resolve external Services associated with a datasource context."""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.models import models


def list_bound_services(db: Session, datasource: models.DataSource | None) -> list[models.Service]:
    """List active Services bound directly or through the datasource cluster key."""
    if datasource is None:
        return []
    refs = [f"datasource:{datasource.id}"]
    if datasource.cluster_key:
        refs.append(f"cluster:{datasource.cluster_key}")
    return (
        db.query(models.Service)
        .options(selectinload(models.Service.knowledge_bases))
        .filter(
            or_(*(models.Service.resource_ref == ref for ref in refs)),
            models.Service.status == "active",
        )
        .order_by(models.Service.id.asc())
        .all()
    )
