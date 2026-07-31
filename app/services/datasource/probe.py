"""
Probe OB cluster/tenant IDs from a live connection and back-fill datasource.attributes.
Only runs on OceanBase 4.x. Failures are silent — never blocks create/update.
"""

import asyncio
from typing import Any

from app.core.logging import fmt_kv, get_logger
from app.db.connection import DBConnectionPool
from app.models import models

logger = get_logger("services.datasource_probe")

_VERSION_SQL = "SELECT @@version_comment AS v"
_CLUSTER_ID_SQL = "SELECT value FROM oceanbase.GV$OB_PARAMETERS WHERE name='cluster_id' LIMIT 1"
_TENANT_ID_SQL = "SELECT effective_tenant_id() AS v"
_PROBE_TIMEOUT = 5


def _is_ob4(version_comment: str) -> bool:
    vc = (version_comment or "").lower()
    return "oceanbase" in vc and any(f" {maj}." in vc or f"/{maj}." in vc for maj in ("4",))


async def _query_scalar(pool: DBConnectionPool, datasource: models.DataSource, sql: str) -> Any:
    result = await asyncio.wait_for(
        pool.execute_query(datasource, sql, role="user"),
        timeout=_PROBE_TIMEOUT,
    )
    rows = result.get("rows") or []
    if not rows:
        return None
    row = rows[0]
    return next(iter(row.values())) if isinstance(row, dict) else row[0]


async def probe_ob_ids(
    datasource: models.DataSource,
    pool: DBConnectionPool,
) -> dict[str, Any]:
    """
    Returns a dict with zero or more of: ob_cluster_id, ob_tenant_id.
    Empty dict means probe was skipped or fully failed.
    """
    try:
        version_comment = await _query_scalar(pool, datasource, _VERSION_SQL)
        if not version_comment or not _is_ob4(str(version_comment)):
            logger.info(
                "datasource_probe_skip_version %s",
                fmt_kv(datasource_id=datasource.id, version_comment=version_comment),
            )
            return {}
    except Exception as exc:
        logger.warning(
            "datasource_probe_version_check_failed %s",
            fmt_kv(datasource_id=datasource.id, error=exc),
        )
        return {}

    result: dict[str, Any] = {}

    async def fetch_cluster_id() -> None:
        try:
            raw = await _query_scalar(pool, datasource, _CLUSTER_ID_SQL)
            if raw is not None:
                result["ob_cluster_id"] = int(raw)
        except Exception as exc:
            logger.warning(
                "datasource_probe_cluster_id_failed %s",
                fmt_kv(datasource_id=datasource.id, error=exc),
            )

    async def fetch_tenant_id() -> None:
        try:
            raw = await _query_scalar(pool, datasource, _TENANT_ID_SQL)
            if raw is not None:
                result["ob_tenant_id"] = int(raw)
        except Exception as exc:
            logger.warning(
                "datasource_probe_tenant_id_failed %s",
                fmt_kv(datasource_id=datasource.id, error=exc),
            )

    await asyncio.gather(fetch_cluster_id(), fetch_tenant_id())
    return result


async def probe_and_fill_ob_ids(
    datasource: models.DataSource,
    pool: DBConnectionPool,
) -> dict[str, Any]:
    """
    Probe and merge ob_cluster_id / ob_tenant_id into datasource.attributes in-place.
    Only fills missing keys — does not overwrite values set by OCP import.
    Returns the probed values (may be empty).
    """
    db_type = (datasource.db_type or "").strip().lower()
    if db_type and db_type != "oceanbase":
        return {}

    try:
        probed = await probe_ob_ids(datasource, pool)
    except Exception as exc:
        logger.warning("datasource_probe_failed %s", fmt_kv(datasource_id=datasource.id, error=exc))
        return {}

    if not probed:
        return {}

    attrs: dict[str, Any] = dict(datasource.attributes or {})
    updated = {k: v for k, v in probed.items() if k not in attrs or attrs[k] is None}
    if not updated:
        return probed

    attrs.update(updated)
    datasource.attributes = attrs
    logger.info(
        "datasource_probe_filled %s",
        fmt_kv(datasource_id=datasource.id, **updated),
    )
    return probed
