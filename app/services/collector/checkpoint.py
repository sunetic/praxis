from datetime import UTC, datetime

from app.db.connection import DBConnectionPool
from app.models.models import DataSource

_pool = DBConnectionPool()

_EXCLUDED_DBS = ("oceanbase", "information_schema", "mysql", "performance_schema", "sys")


async def get_or_create(ds: DataSource, source_type: str) -> dict:
    rows = await _pool.execute_query(
        ds,
        "SELECT id, last_value, status, error_msg FROM collector_checkpoints "
        "WHERE datasource_id = %s AND source_type = %s LIMIT 1",
        params=[ds.id, source_type],
    )
    if rows["row_count"] > 0:
        row = rows["rows"][0]
        if isinstance(row, dict):
            return row
        return dict(zip(rows["columns"], row))
    await _pool.execute_query(
        ds,
        "INSERT INTO collector_checkpoints (datasource_id, source_type, last_value, status) "
        "VALUES (%s, %s, 0, 'idle') ON DUPLICATE KEY UPDATE datasource_id=datasource_id",
        params=[ds.id, source_type],
    )
    return {"id": None, "last_value": 0, "status": "idle", "error_msg": None}


async def update(
    ds: DataSource,
    source_type: str,
    *,
    last_value: int | None = None,
    row_count: int | None = None,
    status: str = "idle",
    error_msg: str | None = None,
) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    parts = ["status = %s", "updated_at = %s"]
    params: list = [status, now]
    if last_value is not None:
        parts.append("last_value = %s")
        params.append(last_value)
    if row_count is not None:
        parts.append("last_row_count = %s")
        params.append(row_count)
    if status in ("idle", "running"):
        parts.append("last_run_at = %s")
        params.append(now)
    parts.append("error_msg = %s")
    params.append(error_msg)
    params += [ds.id, source_type]
    await _pool.execute_query(
        ds,
        f"UPDATE collector_checkpoints SET {', '.join(parts)} "
        "WHERE datasource_id = %s AND source_type = %s",
        params=params,
    )
