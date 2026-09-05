"""Database-agnostic datasource access-level helpers.

Access level describes why a credential is used (regular work or privileged
administration). It is intentionally separate from engine-specific concepts
such as an OceanBase tenant role or a concrete account name like ``root``.
"""

from __future__ import annotations

from typing import Any

USER_ACCESS_LEVEL = "user"
ADMIN_ACCESS_LEVEL = "admin"
SUPPORTED_ACCESS_LEVELS = (USER_ACCESS_LEVEL, ADMIN_ACCESS_LEVEL)

_ACCESS_LEVEL_ALIASES = {
    "business": USER_ACCESS_LEVEL,
    "tenant": USER_ACCESS_LEVEL,
    "user": USER_ACCESS_LEVEL,
    "admin": ADMIN_ACCESS_LEVEL,
    "root": ADMIN_ACCESS_LEVEL,
    "superuser": ADMIN_ACCESS_LEVEL,
    "sys": ADMIN_ACCESS_LEVEL,
}


def normalize_access_level(value: str | None) -> str:
    """Return the canonical user/admin access level.

    Legacy user/sys values remain accepted at boundaries so existing
    datasources and generated Functions continue to work during migration.
    """

    normalized = str(value or USER_ACCESS_LEVEL).strip().lower()
    try:
        return _ACCESS_LEVEL_ALIASES[normalized]
    except KeyError as exc:
        supported = ", ".join(SUPPORTED_ACCESS_LEVELS)
        raise ValueError(f"Unsupported datasource access level '{value}'. Use: {supported}.") from exc


def datasource_access_level(datasource: Any) -> str:
    """Resolve access level from a datasource with legacy fallback."""

    configured = getattr(datasource, "access_level", None)
    legacy_role = str(getattr(datasource, "tenant_role", "") or "").strip().lower()
    if legacy_role in {"sys", "admin", "root", "superuser"}:
        return ADMIN_ACCESS_LEVEL
    if str(configured or "").strip():
        return normalize_access_level(str(configured))
    return normalize_access_level(legacy_role or USER_ACCESS_LEVEL)


def legacy_role_for_access_level(access_level: str) -> str:
    """Map canonical access levels to the legacy connection role contract."""

    return "sys" if normalize_access_level(access_level) == ADMIN_ACCESS_LEVEL else "user"


def normalize_database_family(db_type: str | None) -> str:
    """Normalize supported database aliases for same-cluster routing."""

    normalized = str(db_type or "").strip().lower()
    if normalized == "postgres":
        return "postgresql"
    return normalized


def supports_admin_routing(db_type: str | None) -> bool:
    """Whether automatic user/admin routing is enabled for an engine."""

    return normalize_database_family(db_type) in {"mysql", "postgresql"}
