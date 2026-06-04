from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.connection import get_db_pool
from app.models import models
from app.services.datasource.router import normalize_role


@dataclass(frozen=True)
class FunctionSchemaProbeAttempt:
    datasource_id: int
    role: str
    sql: str
    ok: bool
    row_count: int
    error: str = ""


@dataclass(frozen=True)
class FunctionSchemaProbeResult:
    ran: bool
    reason: str
    datasource_id: int | None
    role: str
    tables: list[str]
    columns_by_table: dict[str, list[str]]
    attempts: list[FunctionSchemaProbeAttempt]
    goal_context: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "reason": self.reason,
            "datasource_id": self.datasource_id,
            "role": self.role,
            "tables": self.tables,
            "columns_by_table": self.columns_by_table,
            "attempts": [
                {
                    "datasource_id": item.datasource_id,
                    "role": item.role,
                    "sql": item.sql,
                    "ok": item.ok,
                    "row_count": item.row_count,
                    "error": item.error,
                }
                for item in self.attempts
            ],
            "goal_context": self.goal_context,
        }


class FunctionSchemaProbe:
    _TABLE_PROBE_SQLS: tuple[str, ...] = (
        "SHOW TABLES",
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() LIMIT 50",
        "SELECT TABLE_NAME FROM information_schema.TABLES LIMIT 50",
    )

    _COLUMN_PROBE_SQLS: tuple[str, ...] = (
        "DESCRIBE `{table}`",
        (
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}' LIMIT 20"
        ),
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_NAME = '{table}' LIMIT 20",
    )

    def probe(
        self,
        *,
        db: Session | None,
        requirement_text: str,
        max_datasources: int = 3,
        max_tables: int = 8,
        max_columns: int = 8,
    ) -> FunctionSchemaProbeResult:
        _ = requirement_text
        if db is None:
            return FunctionSchemaProbeResult(
                ran=False,
                reason="db_unavailable",
                datasource_id=None,
                role="",
                tables=[],
                columns_by_table={},
                attempts=[],
                goal_context="",
            )

        datasources = (
            db.query(models.DataSource)
            .filter(models.DataSource.status == "active")
            .order_by(models.DataSource.updated_at.desc())
            .limit(max(1, min(int(max_datasources or 1), 10)))
            .all()
        )
        if not datasources:
            return FunctionSchemaProbeResult(
                ran=False,
                reason="no_active_datasource",
                datasource_id=None,
                role="",
                tables=[],
                columns_by_table={},
                attempts=[],
                goal_context="",
            )

        attempts: list[FunctionSchemaProbeAttempt] = []
        tables: list[str] = []
        columns_by_table: dict[str, list[str]] = {}
        selected_datasource_id: int | None = None
        selected_role = ""

        for datasource in datasources:
            role_candidates = self._role_candidates(datasource)
            for role in role_candidates:
                for sql in self._TABLE_PROBE_SQLS:
                    rows: list[dict[str, Any]] = []
                    error_text = ""
                    try:
                        result = _run_async_safely(get_db_pool().execute_query(datasource, sql, role=role))
                        raw_rows = result.get("rows") if isinstance(result, dict) else []
                        rows = [item for item in raw_rows if isinstance(item, dict)] if isinstance(raw_rows, list) else []
                    except Exception as exc:
                        error_text = str(exc)
                    attempts.append(
                        FunctionSchemaProbeAttempt(
                            datasource_id=int(datasource.id),
                            role=role,
                            sql=sql,
                            ok=(not error_text),
                            row_count=len(rows),
                            error=error_text,
                        )
                    )
                    if error_text:
                        continue
                    extracted = self._extract_table_names(rows)
                    if extracted:
                        tables = extracted[: max(1, min(int(max_tables or 1), 20))]
                        selected_datasource_id = int(datasource.id)
                        selected_role = role
                        break
                if tables:
                    break
            if tables:
                break

        if not tables or selected_datasource_id is None:
            return FunctionSchemaProbeResult(
                ran=True,
                reason="probe_exhausted_no_table",
                datasource_id=None,
                role="",
                tables=[],
                columns_by_table={},
                attempts=attempts,
                goal_context="",
            )

        datasource = next((item for item in datasources if int(item.id) == selected_datasource_id), None)
        if datasource is None:
            return FunctionSchemaProbeResult(
                ran=True,
                reason="selected_datasource_missing",
                datasource_id=selected_datasource_id,
                role=selected_role,
                tables=tables,
                columns_by_table={},
                attempts=attempts,
                goal_context="",
            )

        for table in tables[:3]:
            escaped = self._escape_sql_literal(table)
            probe_columns: list[str] = []
            for sql_template in self._COLUMN_PROBE_SQLS:
                sql = sql_template.format(table=escaped)
                rows: list[dict[str, Any]] = []
                error_text = ""
                try:
                    result = _run_async_safely(get_db_pool().execute_query(datasource, sql, role=selected_role))
                    raw_rows = result.get("rows") if isinstance(result, dict) else []
                    rows = [item for item in raw_rows if isinstance(item, dict)] if isinstance(raw_rows, list) else []
                except Exception as exc:
                    error_text = str(exc)
                attempts.append(
                    FunctionSchemaProbeAttempt(
                        datasource_id=int(datasource.id),
                        role=selected_role,
                        sql=sql,
                        ok=(not error_text),
                        row_count=len(rows),
                        error=error_text,
                    )
                )
                if error_text:
                    continue
                probe_columns = self._extract_column_names(rows)[: max(1, min(int(max_columns or 1), 20))]
                if probe_columns:
                    break
            if probe_columns:
                columns_by_table[table] = probe_columns

        goal_context = self._build_goal_context(
            datasource_id=selected_datasource_id,
            role=selected_role,
            tables=tables,
            columns_by_table=columns_by_table,
        )
        return FunctionSchemaProbeResult(
            ran=True,
            reason="ok",
            datasource_id=selected_datasource_id,
            role=selected_role,
            tables=tables,
            columns_by_table=columns_by_table,
            attempts=attempts,
            goal_context=goal_context,
        )

    def _role_candidates(self, datasource: models.DataSource) -> list[str]:
        preferred = normalize_role(str(datasource.tenant_role or "user"))
        candidates = [preferred]
        if "sys" not in candidates:
            candidates.append("sys")
        return candidates

    def _extract_table_names(self, rows: list[dict[str, Any]]) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for row in rows:
            candidate = ""
            for key in ("TABLE_NAME", "table_name", "Tables_in_oceanbase", "Tables_in_test"):
                value = row.get(key)
                if isinstance(value, str) and value.strip():
                    candidate = value.strip()
                    break
            if not candidate:
                for value in row.values():
                    if isinstance(value, str) and value.strip():
                        candidate = value.strip()
                        break
            if not candidate:
                continue
            normalized = candidate.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            names.append(candidate)
        return names

    def _extract_column_names(self, rows: list[dict[str, Any]]) -> list[str]:
        """Extract column names with optional type annotation (name:type) from DESCRIBE rows."""
        names: list[str] = []
        seen: set[str] = set()
        for row in rows:
            candidate = ""
            for key in ("Field", "COLUMN_NAME", "column_name"):
                value = row.get(key)
                if isinstance(value, str) and value.strip():
                    candidate = value.strip()
                    break
            if not candidate:
                for key, value in row.items():
                    if isinstance(value, str) and value.strip() and key.lower() not in ("type", "null", "key", "default", "extra"):
                        candidate = value.strip()
                        break
            if not candidate:
                continue
            normalized = candidate.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            col_type = ""
            for type_key in ("Type", "type", "DATA_TYPE", "data_type"):
                raw_type = row.get(type_key)
                if isinstance(raw_type, str) and raw_type.strip():
                    col_type = raw_type.strip().split("(")[0].lower()
                    break
            names.append(f"{candidate}:{col_type}" if col_type else candidate)
        return names

    def _escape_sql_literal(self, value: str) -> str:
        return str(value or "").replace("`", "``").replace("'", "''")

    def _build_goal_context(
        self,
        *,
        datasource_id: int,
        role: str,
        tables: list[str],
        columns_by_table: dict[str, list[str]],
    ) -> str:
        lines: list[str] = [
            "Schema Probe Evidence (auto-discovered):",
            f"- datasource_id={datasource_id}, role={role}",
            f"- candidate_tables={', '.join(tables[:8])}",
        ]
        for table, columns in columns_by_table.items():
            lines.append(f"- {table}.columns={', '.join(columns[:8])}")
        lines.extend(
            [
                "Implementation Rules (from probe):",
                "- Prefer using the tables and columns discovered above; avoid guessing table names.",
                "- If requirements cannot be met by the current tables, output the gaps and proceed to the next exploration round; do not fabricate data.",
                "- Column annotation format is name:type; if type is bigint/int, SQL parameters must be integer, not string.",
            ]
        )
        return "\n".join(lines)


def _run_async_safely(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - forwarded to caller
            error["value"] = exc

    import threading

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "value" in error:
        raise error["value"]
    return result.get("value")
