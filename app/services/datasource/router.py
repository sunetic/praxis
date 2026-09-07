from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import models
from app.services.datasource.access import (
    ADMIN_ACCESS_LEVEL,
    USER_ACCESS_LEVEL,
    datasource_access_level,
    legacy_role_for_access_level,
    normalize_access_level,
    normalize_database_family,
    supports_admin_routing,
)


@dataclass
class RoutedDataSource:
    datasource: models.DataSource
    requested_access_level: str
    resolved_access_level: str
    reason: str

    @property
    def requested_role(self) -> str:
        """Legacy role alias retained for existing runtime consumers."""

        return legacy_role_for_access_level(self.requested_access_level)

    @property
    def resolved_role(self) -> str:
        """Legacy role alias retained for existing connection adapters."""

        return legacy_role_for_access_level(self.resolved_access_level)


class DataSourceRoutingError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "datasource_routing_error",
        cluster_key: str | None = None,
        requested_access_level: str | None = None,
        available_access_levels: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.cluster_key = cluster_key
        self.requested_access_level = requested_access_level
        self.available_access_levels = available_access_levels or []

    def to_payload(self) -> dict[str, object]:
        """Return a structured tool error that an Agent can reason about."""

        return {
            "code": self.code,
            "category": "datasource_routing_error",
            "message": str(self),
            "cluster_key": self.cluster_key,
            "requested_access_level": self.requested_access_level,
            "available_access_levels": self.available_access_levels,
            "retry_hint": (
                "Configure one active admin datasource with the same cluster_key and database "
                "type, or continue with operations available to the user datasource."
            ),
        }


def normalize_role(role: str) -> str:
    """Normalize legacy user/sys role values for compatibility."""

    normalized = (role or "user").strip().lower()
    if normalized == "api":
        return normalized
    try:
        return legacy_role_for_access_level(normalize_access_level(normalized))
    except ValueError as exc:
        raise DataSourceRoutingError(f"Unsupported role: {role}") from exc


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
        db.query(models.DataSource).filter(models.DataSource.id == source_datasource_id).first()
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
        item for item in siblings if datasource_access_level(item) == USER_ACCESS_LEVEL
    ]
    if not user_candidates:
        current_access_level = datasource_access_level(source)
        return RoutedDataSource(
            datasource=source,
            requested_access_level=current_access_level,
            resolved_access_level=current_access_level,
            reason="fallback_source_datasource",
        )

    normalized_db_name = _normalize_match_text(db_name)

    if tenant_id is not None:
        id_matches = [item for item in user_candidates if _ob_tenant_id(item) == tenant_id]
        if normalized_db_name:
            exact_matches = [
                item
                for item in id_matches
                if _normalize_match_text(item.database) == normalized_db_name
            ]
            if exact_matches:
                selected = sorted(exact_matches, key=lambda item: item.id)[0]
                return RoutedDataSource(
                    datasource=selected,
                    requested_access_level=USER_ACCESS_LEVEL,
                    resolved_access_level=USER_ACCESS_LEVEL,
                    reason="matched_by_ob_tenant_id_and_database",
                )
        if id_matches:
            selected = sorted(id_matches, key=lambda item: item.id)[0]
            return RoutedDataSource(
                datasource=selected,
                requested_access_level=USER_ACCESS_LEVEL,
                resolved_access_level=USER_ACCESS_LEVEL,
                reason="matched_by_ob_tenant_id",
            )

    if normalized_db_name:
        db_matches = [
            item
            for item in user_candidates
            if _normalize_match_text(item.database) == normalized_db_name
        ]
        if len(db_matches) == 1:
            selected = db_matches[0]
            return RoutedDataSource(
                datasource=selected,
                requested_access_level=USER_ACCESS_LEVEL,
                resolved_access_level=USER_ACCESS_LEVEL,
                reason="matched_by_database",
            )
        if len(db_matches) > 1:
            raise DataSourceRoutingError(
                f"Multiple user datasources match database '{db_name}' in cluster '{cluster_key}'."
            )

    if datasource_access_level(source) == USER_ACCESS_LEVEL:
        return RoutedDataSource(
            datasource=source,
            requested_access_level=USER_ACCESS_LEVEL,
            resolved_access_level=USER_ACCESS_LEVEL,
            reason="current_datasource_matches_preferred_execution",
        )

    raise DataSourceRoutingError(
        f"Cannot resolve preferred execution datasource in cluster '{cluster_key}' without an unambiguous tenant or database match."
    )


def _active_same_engine_siblings(
    db: Session,
    current: models.DataSource,
    cluster_key: str,
) -> list[models.DataSource]:
    current_family = normalize_database_family(current.db_type)
    rows = (
        db.query(models.DataSource)
        .filter(
            models.DataSource.cluster_key == cluster_key,
            models.DataSource.status == "active",
        )
        .order_by(models.DataSource.id.asc())
        .all()
    )
    return [item for item in rows if normalize_database_family(item.db_type) == current_family]


def list_available_access_levels(
    db: Session,
    current_datasource_id: int,
) -> list[str]:
    """List same-cluster access levels usable from a datasource context."""

    current = db.get(models.DataSource, current_datasource_id)
    if current is None:
        return []
    if not supports_admin_routing(current.db_type):
        return [datasource_access_level(current)]
    cluster_key = ensure_cluster_key(current)
    levels = {
        datasource_access_level(item)
        for item in _active_same_engine_siblings(db, current, cluster_key)
    }
    return [level for level in (USER_ACCESS_LEVEL, ADMIN_ACCESS_LEVEL) if level in levels]


def resolve_datasource_by_access_level(
    db: Session,
    current_datasource_id: int,
    access_level: str = USER_ACCESS_LEVEL,
) -> RoutedDataSource:
    """Resolve a credential datasource within the current logical cluster."""

    try:
        target_access_level = normalize_access_level(access_level)
    except ValueError as exc:
        raise DataSourceRoutingError(str(exc), code="unsupported_access_level") from exc

    current = db.get(models.DataSource, current_datasource_id)
    if not current:
        raise DataSourceRoutingError(f"DataSource {current_datasource_id} not found")

    cluster_key = ensure_cluster_key(current)
    current_access_level = datasource_access_level(current)

    if current_access_level == target_access_level:
        return RoutedDataSource(
            datasource=current,
            requested_access_level=target_access_level,
            resolved_access_level=current_access_level,
            reason="current_datasource_matches_access_level",
        )

    if not supports_admin_routing(current.db_type):
        raise DataSourceRoutingError(
            f"Automatic admin routing is not enabled for database type '{current.db_type}'.",
            code="admin_routing_not_supported",
            cluster_key=cluster_key,
            requested_access_level=target_access_level,
            available_access_levels=[current_access_level],
        )

    siblings = _active_same_engine_siblings(db, current, cluster_key)
    available_access_levels = sorted({datasource_access_level(item) for item in siblings})

    if target_access_level == ADMIN_ACCESS_LEVEL:
        admin_candidates = [
            item for item in siblings if datasource_access_level(item) == ADMIN_ACCESS_LEVEL
        ]
        if not admin_candidates:
            raise DataSourceRoutingError(
                f"No admin datasource is configured for cluster '{cluster_key}'.",
                code="admin_datasource_unavailable",
                cluster_key=cluster_key,
                requested_access_level=target_access_level,
                available_access_levels=available_access_levels,
            )
        if len(admin_candidates) > 1:
            raise DataSourceRoutingError(
                f"Multiple admin datasources are active in cluster '{cluster_key}'.",
                code="admin_datasource_ambiguous",
                cluster_key=cluster_key,
                requested_access_level=target_access_level,
                available_access_levels=available_access_levels,
            )
        selected = admin_candidates[0]
        return RoutedDataSource(
            datasource=selected,
            requested_access_level=target_access_level,
            resolved_access_level=ADMIN_ACCESS_LEVEL,
            reason="resolved_admin_by_cluster_key",
        )

    user_candidates = [
        item for item in siblings if datasource_access_level(item) == USER_ACCESS_LEVEL
    ]
    if not user_candidates:
        raise DataSourceRoutingError(
            f"No user datasource is configured for cluster '{cluster_key}'.",
            code="user_datasource_unavailable",
            cluster_key=cluster_key,
            requested_access_level=target_access_level,
            available_access_levels=available_access_levels,
        )

    if current_ob_tenant_id := _ob_tenant_id(current):
        exact_match = [
            item for item in user_candidates if _ob_tenant_id(item) == current_ob_tenant_id
        ]
        if exact_match:
            selected = sorted(exact_match, key=lambda item: item.id)[0]
            return RoutedDataSource(
                datasource=selected,
                requested_access_level=target_access_level,
                resolved_access_level=USER_ACCESS_LEVEL,
                reason="matched_by_ob_tenant_id",
            )

    current_database = _normalize_match_text(current.database)
    database_matches = [
        item for item in user_candidates if _normalize_match_text(item.database) == current_database
    ]
    if len(database_matches) == 1:
        selected = database_matches[0]
        return RoutedDataSource(
            datasource=selected,
            requested_access_level=target_access_level,
            resolved_access_level=USER_ACCESS_LEVEL,
            reason="matched_user_by_database",
        )
    if len(user_candidates) != 1:
        raise DataSourceRoutingError(
            f"Multiple user datasources are active in cluster '{cluster_key}'; the target is ambiguous.",
            code="user_datasource_ambiguous",
            cluster_key=cluster_key,
            requested_access_level=target_access_level,
            available_access_levels=available_access_levels,
        )

    selected = user_candidates[0]
    return RoutedDataSource(
        datasource=selected,
        requested_access_level=target_access_level,
        resolved_access_level=USER_ACCESS_LEVEL,
        reason="resolved_user_by_cluster_key",
    )


def resolve_datasource_by_role(
    db: Session,
    current_datasource_id: int,
    role: str,
) -> RoutedDataSource:
    """Compatibility wrapper for legacy user/sys callers."""

    try:
        access_level = normalize_access_level(role)
    except ValueError as exc:
        raise DataSourceRoutingError(f"Unsupported role: {role}") from exc
    return resolve_datasource_by_access_level(db, current_datasource_id, access_level)


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
