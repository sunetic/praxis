from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models import models

MONITOR_TABLE_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "sql_audit_stat": {
        "candidates": [
            {
                "table_name": "sql_audit_stat",
                "columns": [
                    "tenant_id",
                    "sql_id",
                    "db_name",
                    "user_name",
                    "bucket_start_us",
                    "executions",
                    "sum_elapsed_us",
                ],
            }
        ],
    },
    "sql_audit_samples": {
        "candidates": [
            {
                "table_name": "sql_audit_samples",
                "columns": ["sql_id", "query_sql", "db_name", "request_time"],
            }
        ],
    },
    "plan_detail_store": {
        "candidates": [
            {
                "table_name": "plan_detail_store",
                "columns": ["sql_id", "plan_id", "plan_hash", "plan_explain"],
            }
        ],
    },
}

MONITOR_FEATURE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "top_sql": ("sql_audit_stat", "sql_audit_samples"),
    "slow_sql": ("sql_audit_stat", "sql_audit_samples"),
    "new_sql": ("sql_audit_samples",),
    "plan_history": ("plan_detail_store",),
    "plan_explain": ("plan_detail_store",),
}


@dataclass(frozen=True)
class MonitorTableResolution:
    logical_name: str
    table_name: str
    present: bool
    columns: list[dict[str, Any]]


async def _inspect_monitor_schema(datasource: models.DataSource | None = None) -> tuple[set[str], set[tuple[str, str]]]:
    from app.db.connection import get_db_pool

    if datasource is None:
        from app.services.sql_analysis.history.queries import _get_monitor_datasource
        datasource = _get_monitor_datasource()
    schema_name = datasource.database or ""
    pool = get_db_pool()
    candidate_table_names = [
        candidate["table_name"]
        for config in MONITOR_TABLE_REQUIREMENTS.values()
        for candidate in config["candidates"]
    ]
    table_rows = await pool.execute_query(
        datasource,
        """
        SELECT TABLE_NAME AS table_name
        FROM information_schema.tables
        WHERE TABLE_SCHEMA = %s
        """,
        role=datasource.tenant_role,
        params=[schema_name],
    )
    available_tables = {
        str(row.get("table_name") or "").lower()
        for row in table_rows.get("rows", [])
        if row.get("table_name")
    }

    placeholders = ", ".join(["%s"] * len(candidate_table_names))
    column_rows = await pool.execute_query(
        datasource,
        f"""
        SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name
        FROM information_schema.columns
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME IN ({placeholders})
        """,
        role=datasource.tenant_role,
        params=[schema_name, *candidate_table_names],
    )
    available_columns = {
        (
            str(row.get("table_name") or "").lower(),
            str(row.get("column_name") or "").lower(),
        )
        for row in column_rows.get("rows", [])
        if row.get("table_name") and row.get("column_name")
    }
    return available_tables, available_columns


def _resolve_requirement(
    logical_name: str,
    *,
    available_tables: set[str],
    available_columns: set[tuple[str, str]],
) -> MonitorTableResolution:
    candidates = MONITOR_TABLE_REQUIREMENTS[logical_name]["candidates"]
    chosen = candidates[0]
    for candidate in candidates:
        table_name = candidate["table_name"]
        table_present = table_name.lower() in available_tables
        if not table_present:
            continue
        chosen = candidate
        if all((table_name.lower(), column.lower()) in available_columns for column in candidate["columns"]):
            break

    table_name = chosen["table_name"]
    present = table_name.lower() in available_tables
    required_columns = [
        {
            "table_name": table_name,
            "column_name": column_name,
            "present": (table_name.lower(), column_name.lower()) in available_columns,
        }
        for column_name in chosen["columns"]
    ]
    return MonitorTableResolution(
        logical_name=logical_name,
        table_name=table_name,
        present=present,
        columns=required_columns,
    )


async def resolve_monitor_table_map(datasource: models.DataSource | None = None) -> dict[str, str]:
    available_tables, available_columns = await _inspect_monitor_schema(datasource)
    resolutions = {
        logical_name: _resolve_requirement(
            logical_name,
            available_tables=available_tables,
            available_columns=available_columns,
        )
        for logical_name in MONITOR_TABLE_REQUIREMENTS
    }
    return {logical_name: resolution.table_name for logical_name, resolution in resolutions.items()}


async def probe_monitor_contract(datasource: models.DataSource) -> dict[str, Any]:
    required_tables: list[dict[str, Any]] = []
    required_columns: list[dict[str, Any]] = []

    try:
        available_tables, available_columns = await _inspect_monitor_schema(datasource)
        resolutions = {
            logical_name: _resolve_requirement(
                logical_name,
                available_tables=available_tables,
                available_columns=available_columns,
            )
            for logical_name in MONITOR_TABLE_REQUIREMENTS
        }
        for resolution in resolutions.values():
            required_tables.append(
                {
                    "logical_name": resolution.logical_name,
                    "table_name": resolution.table_name,
                    "present": resolution.present,
                }
            )
            required_columns.extend(resolution.columns)

        missing_tables = [item["table_name"] for item in required_tables if not item["present"]]
        missing_columns = [
            f"{item['table_name']}.{item['column_name']}"
            for item in required_columns
            if not item["present"]
        ]
        support_matrix = {
            feature: all(
                (
                    resolutions[dependency].present
                    and all(column["present"] for column in resolutions[dependency].columns)
                )
                for dependency in dependencies
            )
            for feature, dependencies in MONITOR_FEATURE_DEPENDENCIES.items()
        }
        return {
            "connection_ok": True,
            "message": None,
            "required_tables": required_tables,
            "missing_tables": missing_tables,
            "required_columns": required_columns,
            "missing_columns": missing_columns,
            "supported_features": support_matrix,
        }
    except Exception as exc:  # pragma: no cover - exercised via API tests
        return {
            "connection_ok": False,
            "message": str(exc),
            "required_tables": required_tables,
            "missing_tables": [item["table_name"] for item in required_tables],
            "required_columns": required_columns,
            "missing_columns": [],
            "supported_features": {feature: False for feature in MONITOR_FEATURE_DEPENDENCIES},
        }
