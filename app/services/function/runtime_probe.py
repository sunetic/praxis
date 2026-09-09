from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from app.core.logging import fmt_kv, get_logger
from app.services.datasource.router import normalize_role

logger = get_logger("services.function.runtime_probe")


class FunctionRuntimeProbe:
    """Validate a Function candidate against a controlled runtime environment."""

    @staticmethod
    def default_payload() -> dict[str, Any]:
        return {
            "datasource_id": 1,
            "datasourceId": 1,
            "datasource_ids": [1, 2],
            "datasourceIds": [1, 2],
            "rows": [],
            "items": [],
            "params": {},
        }

    @staticmethod
    def default_context() -> dict[str, Any]:
        return {
            "datasource_id": 1,
            "scope": {},
            "trace_id": "function-runtime-probe",
            "execution_mode": "plan",
        }

    def run(
        self,
        *,
        workspace_dir: Path,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[bool, str | None, str | None]:
        """Execute the candidate with controlled capabilities and return diagnostics."""
        workspace_resolved = workspace_dir.resolve()
        main_path = workspace_resolved / "main.py"
        logger.info("function_runtime_probe_start %s", fmt_kv(workspace=workspace_resolved))
        if not main_path.exists():
            error = "main.py not found after apply"
            logger.warning("function_runtime_probe_failed %s", fmt_kv(error=error))
            return False, error, None

        code_snapshot = main_path.read_text(encoding="utf-8")
        static_guard_error = self._static_guard_error(code_snapshot)
        if static_guard_error:
            logger.warning("function_runtime_probe_failed %s", fmt_kv(error=static_guard_error))
            return False, static_guard_error, None

        def sample_rows_for_sql(sql_text: str) -> list[dict[str, Any]]:
            normalized = re.sub(r"\s+", " ", str(sql_text or "")).strip().lower()
            if "information_schema.schemata" in normalized:
                return [{"schema_name": "crm"}, {"schema_name": "analytics"}]
            if normalized.startswith("show databases"):
                return [{"Database": "crm"}, {"Database": "analytics"}]
            if normalized.startswith("select"):
                return [{"value": 1}]
            return []

        def sample_query_result(sql_text: str) -> _FakeQueryResult:
            rows = sample_rows_for_sql(sql_text)
            columns = list(rows[0].keys()) if rows else []
            return _FakeQueryResult(columns=columns, rows=rows, row_count=len(rows))

        class _FakeQueryResult(dict):
            def __iter__(self):  # type: ignore[override]
                raise ValueError(
                    "db.query(...) returns a mapping; use result.get('rows', []) "
                    "before iterating rows"
                )

        class _FakeResult:
            def __init__(self, rows: list[dict[str, Any]] | None = None):
                self._rows = rows if isinstance(rows, list) else []

            def mappings(self) -> _FakeResult:
                return self

            def all(self) -> list[dict[str, Any]]:
                return self._rows

        class _FakeSession:
            def execute(self, *args: Any, **_kwargs: Any) -> _FakeResult:
                sql_text = str(args[0]) if args else ""
                return _FakeResult(sample_rows_for_sql(sql_text))

            def close(self) -> None:
                return None

        class _FakeConnection:
            def query(self, sql: str, *, params: list[Any] | None = None) -> _FakeQueryResult:
                _ = params
                return sample_query_result(sql)

            def explain(self, sql: str) -> _FakeQueryResult:
                return sample_query_result(sql)

        class _FakeDB:
            def query(
                self,
                sql: str,
                *,
                datasource: Any = None,
                role: str = "user",
                params: list[Any] | None = None,
            ) -> _FakeQueryResult:
                _ = datasource, params
                _ = normalize_role(role)
                return sample_query_result(sql)

            def explain(
                self, sql: str, *, datasource: Any = None, role: str = "user"
            ) -> _FakeQueryResult:
                _ = datasource
                _ = normalize_role(role)
                return sample_query_result(sql)

            def query_by_id(
                self, *, sql: str, datasource_id: int, params: list[Any] | None = None
            ) -> _FakeQueryResult:
                _ = datasource_id, params
                return sample_query_result(sql)

            def explain_by_id(self, *, sql: str, datasource_id: int) -> _FakeQueryResult:
                _ = datasource_id
                return sample_query_result(sql)

            def get_conn_by_id(self, *, datasource_id: int) -> _FakeConnection:
                _ = datasource_id
                return _FakeConnection()

            def get_session_by_id(self, *, datasource_id: int) -> _FakeSession:
                _ = datasource_id
                return _FakeSession()

            def close_opened_sessions(self) -> None:
                return None

        class _FakePlatform:
            def __init__(self, execution_mode: str) -> None:
                self._execution_mode = str(execution_mode or "apply").strip().lower()

            def list(
                self,
                object_type: str,
                *,
                filters: dict[str, Any] | None = None,
                limit: int = 100,
            ) -> list[dict[str, Any]]:
                _ = filters, limit
                if str(object_type or "").strip().lower() == "datasource":
                    return [
                        {"id": 1, "name": "probe-datasource-1"},
                        {"id": 2, "name": "probe-datasource-2"},
                    ]
                return []

            def get(self, object_type: str, object_id: Any) -> dict[str, Any]:
                _ = object_type, object_id
                return {}

            def crud(
                self,
                *,
                object_type: str,
                action: str,
                object_id: Any = None,
                payload: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                _ = object_type, object_id, payload
                if self._execution_mode == "plan" and str(action or "").strip().lower() in {
                    "create",
                    "update",
                    "delete",
                }:
                    raise ValueError(
                        "Plan mode does not allow control-plane write operations; "
                        "confirm first and use apply mode"
                    )
                return {"ok": True}

            def operate(
                self,
                *,
                object_type: str,
                action: str,
                object_id: Any,
                payload: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                _ = object_type, action, object_id, payload
                if self._execution_mode == "plan":
                    raise ValueError(
                        "Plan mode does not allow control-plane operate actions; "
                        "confirm first and use apply mode"
                    )
                return {"ok": True}

        try:
            from app.services.function.runtime import _execute_code_snapshot

            runtime_result = _execute_code_snapshot(
                code_snapshot=code_snapshot,
                payload=payload,
                context=context,
                runtime_services={
                    "db_capability": _FakeDB(),
                    "platform_capability": _FakePlatform(
                        str(context.get("execution_mode") or "plan")
                    ),
                },
            )
            result_type = (
                type(runtime_result).__name__ if runtime_result is not None else "NoneType"
            )
            logger.info("function_runtime_probe_success %s", fmt_kv(result_type=result_type))
            return True, None, result_type
        except Exception as exc:
            error = f"{exc.__class__.__name__}: {exc}"
            logger.warning("function_runtime_probe_failed %s", fmt_kv(error=error))
            return False, error, None

    @staticmethod
    def repair_hint(error: str) -> str:
        if not str(error or "").strip():
            return ""
        return (
            "Recheck the runtime contract before probing again: "
            "use `main(payload, context)` as the entry signature; "
            "treat `context` as a plain dict and read via `context.get(...)`; "
            "use an explicit datasource_id or discover one via `platform.list('datasource')`; "
            "read query rows from `result.get('rows', [])`; "
            "treat row values as mappings such as `row.get('Database')`; "
            "call `query_by_id` / `explain_by_id` with `datasource_id=...`; "
            "and re-raise DB/platform exceptions after adding context."
        )

    def _static_guard_error(self, code_snapshot: str) -> str | None:
        try:
            syntax_tree = ast.parse(code_snapshot)
        except SyntaxError:
            return None

        for node in ast.walk(syntax_tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = str(node.func.attr)
            if method in {"query_by_id", "explain_by_id"} and len(node.args) > 1:
                line = int(getattr(node, "lineno", 0) or 0)
                return (
                    f"ValueError: Line {line or '?'} `{method}` requires keyword-only "
                    "datasource_id; use datasource_id=..."
                )

        for node in ast.walk(syntax_tree):
            if not isinstance(node, ast.Try) or not self._try_block_has_db_call(node.body):
                continue
            for handler in node.handlers:
                if self._handler_has_raise(handler.body):
                    continue
                line = int(getattr(handler, "lineno", 0) or getattr(node, "lineno", 0) or 0)
                return (
                    f"ValueError: Line {line or '?'} DB exceptions cannot be swallowed; "
                    "raise after handling context"
                )
        return None

    @staticmethod
    def _try_block_has_db_call(body: list[ast.stmt]) -> bool:
        if not body:
            return False
        root = ast.Module(body=body, type_ignores=[])
        for child in ast.walk(root):
            if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                continue
            if str(child.func.attr) in {
                "query",
                "explain",
                "query_by_id",
                "explain_by_id",
                "get_conn_by_id",
                "get_session_by_id",
            }:
                return True
        return False

    @staticmethod
    def _handler_has_raise(body: list[ast.stmt]) -> bool:
        return any(
            isinstance(child, ast.Raise) for statement in body for child in ast.walk(statement)
        )
