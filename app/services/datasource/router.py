from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import models


@dataclass
class RoutedDataSource:
    datasource: models.DataSource
    requested_role: str
    resolved_role: str
    reason: str


class DataSourceRoutingError(ValueError):
    pass


def normalize_role(role: str) -> str:
    normalized = (role or "user").strip().lower()
    if normalized in {"tenant", "business"}:
        normalized = "user"
    if normalized not in {"sys", "user", "api"}:
        raise DataSourceRoutingError(f"Unsupported role: {role}")
    return normalized


def ensure_cluster_key(datasource: models.DataSource) -> str:
    if datasource.cluster_key:
        return datasource.cluster_key
    return f"{datasource.host}:{datasource.port}"


def _normalize_match_text(value: str | None) -> str:
    return str(value or "").strip().lower()


def _ob_tenant_id(datasource: models.DataSource) -> int | None:
    attrs = datasource.attributes if isinstance(datasource.attributes, dict) else {}
    for key in ("ob_tenant_id", "tenant_id"):
        raw = attrs.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def resolve_preferred_execution_datasource(
    db: Session,
    source_datasource_id: int,
    *,
    tenant_id: int | None = None,
    db_name: str | None = None,
) -> RoutedDataSource:
    source = (
        db.query(models.DataSource)
        .filter(models.DataSource.id == source_datasource_id)
        .first()
    )
    if not source:
        raise DataSourceRoutingError(f"DataSource {source_datasource_id} not found")

    cluster_key = ensure_cluster_key(source)
    siblings = (
        db.query(models.DataSource)
        .filter(
            models.DataSource.cluster_key == cluster_key,
            models.DataSource.status == "active",
        )
        .all()
    )
    user_candidates = [
        item for item in siblings if normalize_role(item.tenant_role or "user") == "user"
    ]
    if not user_candidates:
        current_role = normalize_role(source.tenant_role or "user")
        return RoutedDataSource(
            datasource=source,
            requested_role=current_role,
            resolved_role=current_role,
            reason="fallback_source_datasource",
        )

    normalized_db_name = _normalize_match_text(db_name)

    if tenant_id is not None:
        id_matches = [
            item for item in user_candidates if _ob_tenant_id(item) == tenant_id
        ]
        if normalized_db_name:
            exact_matches = [
                item for item in id_matches
                if _normalize_match_text(item.database) == normalized_db_name
            ]
            if exact_matches:
                selected = sorted(exact_matches, key=lambda item: item.id)[0]
                return RoutedDataSource(
                    datasource=selected,
                    requested_role="user",
                    resolved_role="user",
                    reason="matched_by_ob_tenant_id_and_database",
                )
        if id_matches:
            selected = sorted(id_matches, key=lambda item: item.id)[0]
            return RoutedDataSource(
                datasource=selected,
                requested_role="user",
                resolved_role="user",
                reason="matched_by_ob_tenant_id",
            )

    if normalized_db_name:
        db_matches = [
            item for item in user_candidates if _normalize_match_text(item.database) == normalized_db_name
        ]
        if len(db_matches) == 1:
            selected = db_matches[0]
            return RoutedDataSource(
                datasource=selected,
                requested_role="user",
                resolved_role="user",
                reason="matched_by_database",
            )
        if len(db_matches) > 1:
            raise DataSourceRoutingError(
                f"Multiple user datasources match database '{db_name}' in cluster '{cluster_key}'."
            )

    if normalize_role(source.tenant_role or "user") == "user":
        return RoutedDataSource(
            datasource=source,
            requested_role="user",
            resolved_role="user",
            reason="current_datasource_matches_preferred_execution",
        )

    raise DataSourceRoutingError(
        f"Cannot resolve preferred execution datasource in cluster '{cluster_key}' without an unambiguous tenant or database match."
    )


def resolve_datasource_by_role(
    db: Session,
    current_datasource_id: int,
    role: str,
) -> RoutedDataSource:
    target_role = normalize_role(role)

    current = (
        db.query(models.DataSource)
        .filter(models.DataSource.id == current_datasource_id)
        .first()
    )
    if not current:
        raise DataSourceRoutingError(f"DataSource {current_datasource_id} not found")

    cluster_key = ensure_cluster_key(current)
    current_role = normalize_role(current.tenant_role or "user")

    if current_role == target_role:
        return RoutedDataSource(
            datasource=current,
            requested_role=target_role,
            resolved_role=current_role,
            reason="current_datasource_matches_role",
        )

    siblings = (
        db.query(models.DataSource)
        .filter(
            models.DataSource.cluster_key == cluster_key,
            models.DataSource.status == "active",
        )
        .all()
    )

    if target_role == "sys":
        sys_candidates = [item for item in siblings if normalize_role(item.tenant_role or "user") == "sys"]
        if not sys_candidates:
            raise DataSourceRoutingError(
                f"No sys datasource found in cluster '{cluster_key}'."
            )
        if len(sys_candidates) > 1:
            raise DataSourceRoutingError(
                f"Multiple sys datasources found in cluster '{cluster_key}'. Please keep exactly one."
            )
        selected = sys_candidates[0]
        return RoutedDataSource(
            datasource=selected,
            requested_role=target_role,
            resolved_role="sys",
            reason="resolved_by_cluster_key",
        )

    # target_role == user
    user_candidates = [
        item for item in siblings if normalize_role(item.tenant_role or "user") == "user"
    ]
    if not user_candidates:
        raise DataSourceRoutingError(
            f"No user datasource found in cluster '{cluster_key}'."
        )

    if current_ob_tenant_id := _ob_tenant_id(current):
        exact_match = [
            item for item in user_candidates if _ob_tenant_id(item) == current_ob_tenant_id
        ]
        if exact_match:
            selected = sorted(exact_match, key=lambda item: item.id)[0]
            return RoutedDataSource(
                datasource=selected,
                requested_role=target_role,
                resolved_role="user",
                reason="matched_by_ob_tenant_id",
            )

    selected = sorted(user_candidates, key=lambda item: item.id)[0]
    return RoutedDataSource(
        datasource=selected,
        requested_role=target_role,
        resolved_role="user",
        reason="fallback_first_user_in_cluster",
    )


def resolve_collector_datasource(db: Session, cluster_key: str) -> models.DataSource:
    """Return virtual datasource for monitor/collector DB from config settings."""
    from app.core.config import get_settings

    s = get_settings()
    if not s.monitor_db_host:
        raise DataSourceRoutingError(
            "MONITOR_DB_HOST is not configured — cannot resolve collector target"
        )
    return models.DataSource(
        id=0,
        name="__monitor_db__",
        host=s.monitor_db_host,
        port=s.monitor_db_port,
        db_type="oceanbase",
        cluster_key="__config__",
        tenant_role="user",
        user=s.monitor_db_user,
        password=s.monitor_db_password,
        database=s.monitor_db_database,
        status="active",
    )
