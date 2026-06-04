from __future__ import annotations

import asyncio
import json
import multiprocessing
import re
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from typing import Any, TypeVar

from sqlalchemy import create_engine
from sqlalchemy import text as sql_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import fmt_kv, get_logger
from app.db.connection import get_db_pool
from app.db.database import SessionLocal
from app.models import models
from app.services.datasource.router import DataSourceRoutingError, normalize_role, resolve_datasource_by_role
from app.services.function.runtime_contract import get_function_runtime_contract
from app.services.lifecycle import FunctionLifecycleService, LifecycleValidationError

logger = get_logger("function.runtime")
T = TypeVar("T")


class RuntimeErrorClass(StrEnum):
    VALIDATION = "validation"
    RUNTIME = "runtime"
    DEPENDENCY = "dependency"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class RuntimeErrorCode(StrEnum):
    RELEASE_REQUIRED = "release_required"
    DATASOURCE_REQUIRED = "datasource_required"
    SQL_PARAM_PLACEHOLDER = "sql_param_placeholder"
    SQL_SYNTAX_ERROR = "sql_syntax_error"
    SQL_OBJECT_NOT_FOUND = "sql_object_not_found"
    VALIDATION_ERROR = "validation_error"
    TIMEOUT = "timeout"
    DEPENDENCY_ERROR = "dependency_error"


class FunctionRunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class FunctionRuntimeResult:
    run_id: str
    status: str
    output: Any | None
    error_class: str | None
    error_code: str | None
    error_message: str | None
    duration_ms: int


class RuntimeDatasourceAccessError(ValueError):
    pass


class RuntimeDatasourceRequiredError(RuntimeDatasourceAccessError):
    pass


class RuntimePlatformAccessError(ValueError):
    pass


_RUNTIME_CONTRACT = get_function_runtime_contract()
_DB_METHOD_SCHEMAS = dict((((_RUNTIME_CONTRACT.get("db_api") or {}).get("schemas") or {})))
_PLATFORM_CONTRACT = (_RUNTIME_CONTRACT.get("platform_api") or {})
_PLATFORM_LIST_FILTER_SCHEMAS = dict((_PLATFORM_CONTRACT.get("list_filter_schemas") or {}))
_PLATFORM_CRUD_PAYLOAD_SCHEMAS = dict((_PLATFORM_CONTRACT.get("crud_payload_schemas") or {}))
_PLATFORM_OPERATE_PAYLOAD_SCHEMAS = dict((_PLATFORM_CONTRACT.get("operate_payload_schemas") or {}))
_DB_ROLE_ENUM = set((((_RUNTIME_CONTRACT.get("db_api") or {}).get("role_enum")) or []))


def _resolve_runtime_schema_ref(ref: str) -> dict[str, Any]:
    current: Any = _RUNTIME_CONTRACT
    for part in str(ref or "").split("."):
        if not isinstance(current, dict):
            raise KeyError(ref)
        current = current.get(part)
    if not isinstance(current, dict):
        raise KeyError(ref)
    return current


def _resolve_runtime_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    if "$ref" in schema:
        return _resolve_runtime_schema_ref(str(schema.get("$ref") or ""))
    return schema


def _validate_runtime_value(
    *,
    schema: dict[str, Any] | None,
    value: Any,
    path: str,
    error_cls: type[Exception],
) -> None:
    resolved = _resolve_runtime_schema(schema)
    if not resolved:
        return
    if "one_of" in resolved:
        branches = resolved.get("one_of")
        if isinstance(branches, list):
            branch_errors: list[str] = []
            for branch in branches:
                try:
                    _validate_runtime_value(schema=branch, value=value, path=path, error_cls=error_cls)
                    return
                except Exception as exc:  # pragma: no cover - branch detail only
                    branch_errors.append(str(exc))
            raise error_cls(branch_errors[0] if branch_errors else f"{path} does not match any allowed schema")
    schema_type = resolved.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            raise error_cls(f"{path} must be an object")
        properties = resolved.get("properties") if isinstance(resolved.get("properties"), dict) else {}
        additional_properties = resolved.get("additional_properties", True)
        if additional_properties is False:
            unknown = sorted(set(value.keys()) - set(properties.keys()))
            if unknown:
                raise error_cls(f"{path} contains undeclared fields: {', '.join(str(item) for item in unknown)}")
        constraints = resolved.get("constraints") if isinstance(resolved.get("constraints"), dict) else {}
        required = constraints.get("required") if isinstance(constraints.get("required"), list) else []
        missing = [str(name) for name in required if value.get(str(name)) is None]
        if missing:
            raise error_cls(f"{path} is missing required fields: {', '.join(missing)}")
        for key, child_schema in properties.items():
            if key in value and value.get(key) is not None:
                _validate_runtime_value(
                    schema=child_schema if isinstance(child_schema, dict) else {},
                    value=value.get(key),
                    path=f"{path}.{key}",
                    error_cls=error_cls,
                )
        return
    if schema_type == "array":
        if not isinstance(value, list):
            raise error_cls(f"{path} must be an array")
        min_items = resolved.get("min_items")
        max_items = resolved.get("max_items")
        if isinstance(min_items, int) and len(value) < min_items:
            raise error_cls(f"{path} requires at least {min_items} items")
        if isinstance(max_items, int) and len(value) > max_items:
            raise error_cls(f"{path} allows at most {max_items} items")
        item_schema = resolved.get("items") if isinstance(resolved.get("items"), dict) else {}
        for index, item in enumerate(value):
            _validate_runtime_value(
                schema=item_schema,
                value=item,
                path=f"{path}[{index}]",
                error_cls=error_cls,
            )
        return
    if schema_type == "string":
        if not isinstance(value, str):
            raise error_cls(f"{path} must be a string")
        min_length = resolved.get("min_length")
        if isinstance(min_length, int) and len(value.strip()) < min_length:
            raise error_cls(f"{path} must not be empty")
        enum_values = resolved.get("enum")
        if isinstance(enum_values, list) and value not in enum_values:
            raise error_cls(f"{path} contains undeclared value: {value}")
        return
    if schema_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise error_cls(f"{path} must be an integer")
        minimum = resolved.get("minimum")
        maximum = resolved.get("maximum")
        if isinstance(minimum, int) and value < minimum:
            raise error_cls(f"{path} must be >= {minimum}")
        if isinstance(maximum, int) and value > maximum:
            raise error_cls(f"{path} must be <= {maximum}")
        return
    if schema_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise error_cls(f"{path} must be a number")
        minimum = resolved.get("minimum")
        maximum = resolved.get("maximum")
        if isinstance(minimum, (int, float)) and float(value) < float(minimum):
            raise error_cls(f"{path} must be >= {minimum}")
        if isinstance(maximum, (int, float)) and float(value) > float(maximum):
            raise error_cls(f"{path} must be <= {maximum}")
        return
    if schema_type == "boolean":
        if not isinstance(value, bool):
            raise error_cls(f"{path} must be a boolean")
        return
    if schema_type == "null":
        if value is not None:
            raise error_cls(f"{path} must be null")
        return


@lru_cache(maxsize=8)
def _get_control_session_factory(control_db_url: str) -> sessionmaker[Session]:
    connect_args = {"check_same_thread": False} if "sqlite" in control_db_url else {}
    engine = create_engine(control_db_url, connect_args=connect_args)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def _run_coroutine_sync(coroutine: Awaitable[T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    raise RuntimeDatasourceAccessError(
        "DB capability only supports synchronous function runtime; async event loop context is not supported"
    )


@dataclass(frozen=True)
class _ResolvedDatasource:
    datasource: models.DataSource
    resolved_role: str
    requested_datasource_id: int


class _RuntimeDatasourceBroker:
    def __init__(
        self,
        *,
        control_db_url: str | None,
        default_datasource_id: int | None,
    ):
        self._default_datasource_id = default_datasource_id
        self._control_db_url = control_db_url

    def resolve(
        self,
        *,
        datasource: int | str | None = None,
        role: str = "user",
    ) -> _ResolvedDatasource:
        target_role = normalize_role(role)
        db = self._open_db()
        try:
            requested_id = self._resolve_datasource_id(db, datasource)
            routed = resolve_datasource_by_role(db, requested_id, target_role)
            return _ResolvedDatasource(
                datasource=routed.datasource,
                resolved_role=routed.resolved_role,
                requested_datasource_id=requested_id,
            )
        except DataSourceRoutingError as exc:
            raise RuntimeDatasourceAccessError(str(exc)) from exc
        finally:
            db.close()

    def _open_db(self) -> Session:
        if self._control_db_url:
            factory = _get_control_session_factory(self._control_db_url)
            return factory()
        return SessionLocal()

    def _resolve_datasource_id(self, db: Session, datasource: int | str | None) -> int:
        if datasource is None or datasource == "":
            resolved_default = self._resolve_default_datasource_id()
            if resolved_default is None:
                raise RuntimeDatasourceRequiredError(
                    "Missing datasource binding for this invocation; pass datasource_id or bind a default datasource first"
                )
            return resolved_default

        if isinstance(datasource, int):
            return datasource

        normalized = str(datasource).strip()
        if not normalized:
            resolved_default = self._resolve_default_datasource_id()
            if resolved_default is None:
                raise RuntimeDatasourceRequiredError(
                    "Missing datasource binding for this invocation; pass datasource_id or bind a default datasource first"
                )
            return resolved_default
        if normalized.lower() in {"default", "current"}:
            resolved_default = self._resolve_default_datasource_id()
            if resolved_default is None:
                raise RuntimeDatasourceRequiredError(
                    "Missing datasource binding for this invocation; pass datasource_id or bind a default datasource first"
                )
            return resolved_default
        if normalized.isdigit():
            return int(normalized)

        datasource_row = (
            db.query(models.DataSource)
            .filter(
                models.DataSource.name == normalized,
                models.DataSource.status == "active",
            )
            .order_by(models.DataSource.id.asc())
            .first()
        )
        if datasource_row is None:
            raise RuntimeDatasourceAccessError(f"Datasource '{normalized}' not found or inactive")
        return datasource_row.id

    def _resolve_default_datasource_id(self) -> int | None:
        if self._default_datasource_id is not None:
            return self._default_datasource_id
        return None

    def get_by_id(self, datasource_id: int) -> models.DataSource:
        db = self._open_db()
        try:
            item = (
                db.query(models.DataSource)
                .filter(
                    models.DataSource.id == int(datasource_id),
                    models.DataSource.status == "active",
                )
                .first()
            )
            if item is None:
                raise RuntimeDatasourceAccessError(f"Datasource '{datasource_id}' not found or inactive")
            return item
        finally:
            db.close()


class _RuntimeDatasourceConnection:
    def __init__(
        self,
        capability: "_RuntimeDatabaseCapability",
        *,
        datasource_id: int,
    ):
        self._capability = capability
        self._datasource_id = datasource_id

    def query(self, sql: str, *, params: list[Any] | None = None) -> dict[str, Any]:
        return self._capability.query_by_id(sql, datasource_id=self._datasource_id, params=params)

    def explain(self, sql: str) -> dict[str, Any]:
        return self._capability.explain_by_id(sql, datasource_id=self._datasource_id)


class _RuntimeDatabaseCapability:
    def __init__(self, broker: _RuntimeDatasourceBroker, *, execution_mode: str = "apply"):
        self._broker = broker
        self._execution_mode = execution_mode
        self._opened_sessions: list[Session] = []

    def query(
        self,
        sql: str,
        *,
        datasource: int | str | None = None,
        role: str = "user",
        params: list[Any] | None = None,
    ) -> dict[str, Any]:
        _validate_runtime_value(
            schema=_DB_METHOD_SCHEMAS.get("query"),
            value={"sql": sql, "datasource": datasource, "role": role, "params": params},
            path="db.query",
            error_cls=RuntimeDatasourceAccessError,
        )
        self._ensure_query_allowed(sql)
        query_sql = str(sql or "")
        query_params = params
        if query_params is not None and "?" in query_sql:
            # aiomysql/pymysql parameter style is `%s`, while builder prompts may generate `?`.
            # Normalize qmark placeholders to avoid runtime string-formatting errors.
            query_sql = query_sql.replace("?", "%s")
        resolved = self._broker.resolve(datasource=datasource, role=role)
        result = _run_coroutine_sync(
            get_db_pool().execute_query(
                resolved.datasource,
                query_sql,
                role=resolved.resolved_role,
                params=query_params,
            )
        )
        return {
            **result,
            "resolved_datasource_id": resolved.datasource.id,
            "requested_datasource_id": resolved.requested_datasource_id,
            "resolved_role": resolved.resolved_role,
        }

    def explain(
        self,
        sql: str,
        *,
        datasource: int | str | None = None,
        role: str = "user",
    ) -> dict[str, Any]:
        _validate_runtime_value(
            schema=_DB_METHOD_SCHEMAS.get("explain"),
            value={"sql": sql, "datasource": datasource, "role": role},
            path="db.explain",
            error_cls=RuntimeDatasourceAccessError,
        )
        resolved = self._broker.resolve(datasource=datasource, role=role)
        result = _run_coroutine_sync(
            get_db_pool().execute_explain(
                resolved.datasource,
                sql,
                role=resolved.resolved_role,
            )
        )
        return {
            **result,
            "resolved_datasource_id": resolved.datasource.id,
            "requested_datasource_id": resolved.requested_datasource_id,
            "resolved_role": resolved.resolved_role,
        }

    def get_conn_by_id(self, datasource_id: int) -> _RuntimeDatasourceConnection:
        _validate_runtime_value(
            schema=_DB_METHOD_SCHEMAS.get("get_conn_by_id"),
            value={"datasource_id": datasource_id},
            path="db.get_conn_by_id",
            error_cls=RuntimeDatasourceAccessError,
        )
        return _RuntimeDatasourceConnection(
            self,
            datasource_id=int(datasource_id),
        )

    def get_session_by_id(self, datasource_id: int) -> Session:
        _validate_runtime_value(
            schema=_DB_METHOD_SCHEMAS.get("get_session_by_id"),
            value={"datasource_id": datasource_id},
            path="db.get_session_by_id",
            error_cls=RuntimeDatasourceAccessError,
        )
        datasource = self._broker.get_by_id(int(datasource_id))
        sqlalchemy_url = _build_sqlalchemy_url_for_datasource(datasource)
        factory = _get_runtime_session_factory(sqlalchemy_url)
        session = factory()
        self._opened_sessions.append(session)
        return session

    def query_by_id(
        self,
        sql: str,
        *,
        datasource_id: int,
        params: list[Any] | None = None,
    ) -> dict[str, Any]:
        _validate_runtime_value(
            schema=_DB_METHOD_SCHEMAS.get("query_by_id"),
            value={"sql": sql, "datasource_id": datasource_id, "params": params},
            path="db.query_by_id",
            error_cls=RuntimeDatasourceAccessError,
        )
        self._ensure_query_allowed(sql)
        query_sql = str(sql or "")
        query_params = params
        if query_params is not None and "?" in query_sql:
            query_sql = query_sql.replace("?", "%s")
        datasource = self._broker.get_by_id(int(datasource_id))
        resolved_role = _role_for_datasource(datasource)
        result = _run_coroutine_sync(
            get_db_pool().execute_query(
                datasource,
                query_sql,
                role=resolved_role,
                params=query_params,
            )
        )
        return {
            **result,
            "resolved_datasource_id": datasource.id,
            "requested_datasource_id": int(datasource_id),
            "resolved_role": resolved_role,
        }

    def explain_by_id(
        self,
        sql: str,
        *,
        datasource_id: int,
    ) -> dict[str, Any]:
        _validate_runtime_value(
            schema=_DB_METHOD_SCHEMAS.get("explain_by_id"),
            value={"sql": sql, "datasource_id": datasource_id},
            path="db.explain_by_id",
            error_cls=RuntimeDatasourceAccessError,
        )
        datasource = self._broker.get_by_id(int(datasource_id))
        resolved_role = _role_for_datasource(datasource)
        result = _run_coroutine_sync(
            get_db_pool().execute_explain(
                datasource,
                sql,
                role=resolved_role,
            )
        )
        return {
            **result,
            "resolved_datasource_id": datasource.id,
            "requested_datasource_id": int(datasource_id),
            "resolved_role": resolved_role,
        }

    def close_opened_sessions(self) -> None:
        while self._opened_sessions:
            session = self._opened_sessions.pop()
            try:
                session.close()
            except Exception:
                continue

    def _ensure_query_allowed(self, sql: str) -> None:
        if self._execution_mode != "plan":
            return
        normalized = re.sub(r"\s+", " ", str(sql or "")).strip().lower()
        if not normalized:
            raise RuntimeDatasourceAccessError("SQL must not be empty")
        if normalized.startswith(("select", "with", "explain", "show", "describe", "desc")):
            return
        raise RuntimeDatasourceAccessError("Write SQL is forbidden in plan mode; confirm and use apply mode to execute")


class _RuntimePlatformCapability:
    _MUTATING_CRUD_ACTIONS = {"create", "update", "delete"}

    def __init__(self, *, control_db_url: str | None, execution_mode: str = "apply"):
        self._execution_mode = str(execution_mode or "apply").strip().lower()
        if control_db_url:
            self._session_factory = _get_control_session_factory(control_db_url)
        else:
            self._session_factory = SessionLocal
        self._object_service: Any | None = None

    def list(
        self,
        object_type: str,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        _validate_runtime_value(
            schema=(_PLATFORM_CONTRACT.get("schemas") or {}).get("list"),
            value={"object_type": object_type, "filters": filters, "limit": limit},
            path="platform.list",
            error_cls=RuntimePlatformAccessError,
        )
        self._validate_platform_filters(object_type=object_type, filters=filters)
        result = self.crud(object_type=object_type, action="list", payload=filters or {})
        rows = result.get("items") if isinstance(result, dict) else []
        items = [item for item in rows if isinstance(item, dict)]
        filter_map = filters if isinstance(filters, dict) else {}
        if filter_map:
            filtered: list[dict[str, Any]] = []
            for item in items:
                if all(item.get(key) == value for key, value in filter_map.items()):
                    filtered.append(item)
            items = filtered
        normalized_limit = max(1, min(int(limit or 100), 1000))
        return items[:normalized_limit]

    def get(self, object_type: str, object_id: int) -> dict[str, Any]:
        _validate_runtime_value(
            schema=(_PLATFORM_CONTRACT.get("schemas") or {}).get("get"),
            value={"object_type": object_type, "object_id": object_id},
            path="platform.get",
            error_cls=RuntimePlatformAccessError,
        )
        return self.crud(object_type=object_type, action="read", object_id=int(object_id))

    def crud(
        self,
        *,
        object_type: str,
        action: str,
        object_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        normalized_payload = payload or {}
        normalized_type = str(object_type or "").strip().lower()
        self._ensure_crud_allowed(
            object_type=normalized_type,
            action=normalized_action,
            payload=normalized_payload,
        )
        _validate_runtime_value(
            schema=(_PLATFORM_CONTRACT.get("schemas") or {}).get("crud"),
            value={
                "object_type": normalized_type,
                "action": normalized_action,
                "object_id": object_id,
                "payload": normalized_payload,
            },
            path="platform.crud",
            error_cls=RuntimePlatformAccessError,
        )
        self._validate_platform_crud_payload(
            object_type=normalized_type,
            action=normalized_action,
            object_id=object_id,
            payload=normalized_payload,
        )
        service = self._get_object_service()
        try:
            return _run_coroutine_sync(
                service.crud(
                    object_type=object_type,
                    action=normalized_action,
                    object_id=object_id,
                    payload=normalized_payload,
                    actor="function_runtime",
                )
            )
        except Exception as exc:
            raise RuntimePlatformAccessError(str(exc)) from exc

    def operate(
        self,
        *,
        object_type: str,
        action: str,
        object_id: int,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_type = str(object_type or "").strip().lower()
        normalized_action = str(action or "").strip().lower()
        normalized_payload = payload or {}
        _validate_runtime_value(
            schema=(_PLATFORM_CONTRACT.get("schemas") or {}).get("operate"),
            value={
                "object_type": normalized_type,
                "action": normalized_action,
                "object_id": object_id,
                "payload": normalized_payload,
            },
            path="platform.operate",
            error_cls=RuntimePlatformAccessError,
        )
        self._validate_platform_operate_payload(
            object_type=normalized_type,
            action=normalized_action,
            payload=normalized_payload,
        )
        self._ensure_operate_allowed()
        service = self._get_object_service()
        try:
            return _run_coroutine_sync(
                service.operate(
                    object_type=object_type,
                    action=action,
                    object_id=int(object_id),
                    payload=payload or {},
                    actor="function_runtime",
                )
            )
        except Exception as exc:
            raise RuntimePlatformAccessError(str(exc)) from exc

    def _get_object_service(self):
        if self._object_service is not None:
            return self._object_service
        # Import lazily to avoid circular import during module initialization.
        from app.services.platform.object_tools import ObjectToolService

        self._object_service = ObjectToolService(session_factory=self._session_factory)
        return self._object_service

    def _ensure_crud_allowed(
        self,
        *,
        object_type: str,
        action: str,
        payload: dict[str, Any],
    ) -> None:
        if self._execution_mode != "plan":
            return
        if (
            object_type == "scheduler_history"
            and action == "delete"
            and bool(payload.get("dry_run")) is True
        ):
            return
        if action in self._MUTATING_CRUD_ACTIONS:
            raise RuntimePlatformAccessError("Control plane write operations are forbidden in plan mode; confirm and use apply mode to execute")

    def _ensure_operate_allowed(self) -> None:
        if self._execution_mode != "plan":
            return
        raise RuntimePlatformAccessError("Control plane operate actions are forbidden in plan mode; confirm and use apply mode to execute")

    def _validate_platform_filters(self, *, object_type: str, filters: dict[str, Any] | None) -> None:
        if filters is None:
            return
        schema = _PLATFORM_LIST_FILTER_SCHEMAS.get(str(object_type or "").strip().lower())
        if not isinstance(schema, dict):
            return
        _validate_runtime_value(
            schema=schema,
            value=filters,
            path=f"platform.list[{object_type}].filters",
            error_cls=RuntimePlatformAccessError,
        )

    def _validate_platform_crud_payload(
        self,
        *,
        object_type: str,
        action: str,
        object_id: int | None,
        payload: dict[str, Any],
    ) -> None:
        payload_map = _PLATFORM_CRUD_PAYLOAD_SCHEMAS.get(object_type)
        if not isinstance(payload_map, dict):
            return
        schema = payload_map.get(action)
        if isinstance(schema, dict):
            _validate_runtime_value(
                schema=schema,
                value=payload,
                path=f"platform.crud[{object_type}.{action}].payload",
                error_cls=RuntimePlatformAccessError,
            )
        if (
            action in {"read", "update", "delete"}
            and not isinstance(object_id, int)
            and not (object_type == "scheduler_history" and action in {"list", "delete"})
        ):
            raise RuntimePlatformAccessError(f"platform.crud[{object_type}.{action}] is missing object_id")

    def _validate_platform_operate_payload(
        self,
        *,
        object_type: str,
        action: str,
        payload: dict[str, Any],
    ) -> None:
        payload_map = _PLATFORM_OPERATE_PAYLOAD_SCHEMAS.get(object_type)
        if not isinstance(payload_map, dict):
            return
        schema = payload_map.get(action)
        if not isinstance(schema, dict):
            return
        _validate_runtime_value(
            schema=schema,
            value=payload,
            path=f"platform.operate[{object_type}.{action}].payload",
            error_cls=RuntimePlatformAccessError,
        )


class FunctionBase:
    """
    Internal runtime base class for class-style function implementations.
    """

    def __init__(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        db: _RuntimeDatabaseCapability,
        platform: _RuntimePlatformCapability | None = None,
        scheduler_history: "_RuntimeSchedulerHistoryCapability" | None = None,
    ):
        self.payload = payload
        self.context = context
        self.db = db
        self.platform = platform
        self.scheduler_history = scheduler_history

    def get_conn_by_id(self, datasource_id: int) -> _RuntimeDatasourceConnection:
        return self.db.get_conn_by_id(datasource_id=datasource_id)

    def get_session_by_id(self, datasource_id: int) -> Session:
        return self.db.get_session_by_id(datasource_id=datasource_id)

    def run(self, payload: dict[str, Any], context: dict[str, Any]) -> Any:  # pragma: no cover - interface only
        raise NotImplementedError


def _build_db_capability(
    context: dict[str, Any],
    runtime_services: dict[str, Any] | None,
) -> _RuntimeDatabaseCapability:
    services = runtime_services or {}
    if "db_capability" in services:
        return services["db_capability"]

    default_datasource_id = context.get("datasource_id")
    broker = _RuntimeDatasourceBroker(
        control_db_url=services.get("control_db_url"),
        default_datasource_id=default_datasource_id if isinstance(default_datasource_id, int) else None,
    )
    execution_mode = str(context.get("execution_mode") or "apply")
    return _RuntimeDatabaseCapability(broker, execution_mode=execution_mode)


def _build_platform_capability(
    context: dict[str, Any],
    runtime_services: dict[str, Any] | None,
) -> _RuntimePlatformCapability:
    services = runtime_services or {}
    if "platform_capability" in services:
        return services["platform_capability"]
    execution_mode = str(context.get("execution_mode") or "apply")
    return _RuntimePlatformCapability(
        control_db_url=services.get("control_db_url"),
        execution_mode=execution_mode,
    )


class _RuntimeSchedulerHistoryCapability:
    STATUS_ENUM = ("queued", "running", "retrying", "success", "failed")

    def __init__(self, platform: _RuntimePlatformCapability, *, execution_mode: str = "apply"):
        self._platform = platform
        self._execution_mode = str(execution_mode or "apply").strip().lower()

    def list(
        self,
        *,
        where: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"limit": int(limit)}
        if where is not None:
            payload["where"] = self._normalize_where(where)
        result = self._platform.crud(object_type="scheduler_history", action="list", payload=payload)
        rows = result.get("items") if isinstance(result, dict) else []
        return [item for item in rows if isinstance(item, dict)]

    def delete(
        self,
        *,
        where: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if self._execution_mode == "plan" and not dry_run:
            raise RuntimePlatformAccessError("scheduler_history.delete only allows dry_run=True in plan mode")
        payload: dict[str, Any] = {"dry_run": bool(dry_run)}
        if where is not None:
            payload["where"] = self._normalize_where(where)
        if policy is not None:
            payload["policy"] = self._normalize_policy(policy)
        return self._platform.crud(object_type="scheduler_history", action="delete", payload=payload)

    def _normalize_where(self, where: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(where, dict):
            raise RuntimePlatformAccessError("scheduler_history.where must be an object")
        normalized: dict[str, Any] = {}
        if "schedule_id" in where and where.get("schedule_id") is not None:
            normalized["schedule_id"] = int(where["schedule_id"])
        if "statuses" in where and where.get("statuses") is not None:
            statuses = where["statuses"]
            if not isinstance(statuses, list):
                raise RuntimePlatformAccessError("scheduler_history.where.statuses must be a string array")
            normalized_statuses = [str(item or "").strip().lower() for item in statuses if str(item or "").strip()]
            unknown = [item for item in normalized_statuses if item not in self.STATUS_ENUM]
            if unknown:
                raise RuntimePlatformAccessError(
                    f"scheduler_history.where.statuses contains undeclared values: {', '.join(sorted(set(unknown)))}"
                )
            normalized["statuses"] = normalized_statuses
        unknown_keys = sorted(set(where.keys()) - {"schedule_id", "statuses"})
        if unknown_keys:
            raise RuntimePlatformAccessError(
                f"scheduler_history.where contains undeclared fields: {', '.join(unknown_keys)}"
            )
        return normalized

    def _normalize_policy(self, policy: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(policy, dict):
            raise RuntimePlatformAccessError("scheduler_history.policy must be an object")
        normalized: dict[str, Any] = {}
        if "retention_seconds" in policy and policy.get("retention_seconds") is not None:
            normalized["retention_seconds"] = int(policy["retention_seconds"])
        if "keep_latest" in policy and policy.get("keep_latest") is not None:
            normalized["keep_latest"] = int(policy["keep_latest"])
        unknown_keys = sorted(set(policy.keys()) - {"retention_seconds", "keep_latest"})
        if unknown_keys:
            raise RuntimePlatformAccessError(
                f"scheduler_history.policy contains undeclared fields: {', '.join(unknown_keys)}"
            )
        return normalized


def _build_scheduler_history_capability(
    context: dict[str, Any],
    runtime_services: dict[str, Any] | None,
    *,
    platform_capability: _RuntimePlatformCapability,
) -> _RuntimeSchedulerHistoryCapability:
    services = runtime_services or {}
    if "scheduler_history_capability" in services:
        return services["scheduler_history_capability"]
    execution_mode = str(context.get("execution_mode") or "apply")
    return _RuntimeSchedulerHistoryCapability(
        platform_capability,
        execution_mode=execution_mode,
    )


def _role_for_datasource(datasource: models.DataSource) -> str:
    try:
        return normalize_role(datasource.tenant_role or "user")
    except Exception:
        return "user"


def _build_sqlalchemy_url_for_datasource(datasource: models.DataSource) -> str:
    user = datasource.user or ""
    password = datasource.password or ""
    database = datasource.database or ""

    if not user:
        raise RuntimeDatasourceAccessError("Datasource user is missing for runtime session")

    sqlalchemy_url = URL.create(
        drivername="mysql+pymysql",
        username=user,
        password=password,
        host=datasource.host,
        port=int(datasource.port),
        database=database,
    )
    return sqlalchemy_url.render_as_string(hide_password=False)


@lru_cache(maxsize=32)
def _get_runtime_session_factory(sqlalchemy_url: str) -> sessionmaker[Session]:
    engine = create_engine(sqlalchemy_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def _pick_function_class(namespace: dict[str, Any]) -> type[FunctionBase] | None:
    candidate: type[FunctionBase] | None = None
    for value in namespace.values():
        if isinstance(value, type) and issubclass(value, FunctionBase) and value is not FunctionBase:
            candidate = value
    return candidate


def _execute_code_snapshot(
    code_snapshot: str,
    payload: dict[str, Any],
    context: dict[str, Any],
    runtime_services: dict[str, Any] | None = None,
) -> Any:
    db_capability = _build_db_capability(context, runtime_services)
    platform_capability = _build_platform_capability(context, runtime_services)
    scheduler_history_capability = _build_scheduler_history_capability(
        context,
        runtime_services,
        platform_capability=platform_capability,
    )
    runtime_context = dict(context)
    runtime_context.setdefault("db", db_capability)
    runtime_context.setdefault("platform", platform_capability)
    runtime_context.setdefault("scheduler_history", scheduler_history_capability)
    namespace: dict[str, Any] = {
        "__builtins__": __builtins__,
        "payload": payload,
        "context": runtime_context,
        "db": db_capability,
        "platform": platform_capability,
        "scheduler_history": scheduler_history_capability,
        "text": sql_text,
        "FunctionBase": FunctionBase,
        "result": None,
    }
    try:
        # This runtime is intentionally constrained to release code snapshots that
        # are generated/verified by the platform.
        # Use one shared namespace for globals/locals so names like `text` are
        # visible inside class method bodies defined by exec().
        exec(code_snapshot, namespace, namespace)
        main_callable = namespace.get("main")
        if callable(main_callable):
            return main_callable(payload, runtime_context)
        function_class = _pick_function_class(namespace)
        if function_class is not None:
            try:
                instance = function_class(
                    payload,
                    runtime_context,
                    db_capability,
                    platform_capability,
                    scheduler_history_capability,
                )
            except TypeError:
                try:
                    instance = function_class(payload, runtime_context, db_capability)
                except TypeError:
                    instance = function_class(payload, runtime_context)  # type: ignore[misc]
            setattr(instance, "platform", platform_capability)
            setattr(instance, "db", db_capability)
            setattr(instance, "scheduler_history", scheduler_history_capability)
            setattr(instance, "payload", payload)
            setattr(instance, "context", runtime_context)
            runner = getattr(instance, "run", None)
            if callable(runner):
                return runner(payload, runtime_context)
        return namespace.get("result")
    finally:
        close_sessions = getattr(db_capability, "close_opened_sessions", None)
        if callable(close_sessions):
            close_sessions()


class FunctionRuntimeService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] | Callable[[], Session] = SessionLocal,
        lifecycle_service: FunctionLifecycleService | None = None,
        max_workers: int = 1,
    ):
        self._session_factory = session_factory
        self._lifecycle = lifecycle_service or FunctionLifecycleService()
        self._executor = ProcessPoolExecutor(
            max_workers=max_workers,
            mp_context=multiprocessing.get_context("spawn"),
        )

    def bind_runtime_context(
        self,
        payload: dict[str, Any],
        *,
        datasource_id: int | None = None,
        scope_metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        normalized_scope = scope_metadata or {}
        bound_context = {
            "datasource_id": datasource_id,
            "scope": normalized_scope,
        }
        execution_mode = normalized_scope.get("execution_mode") if isinstance(normalized_scope, dict) else None
        if isinstance(execution_mode, str) and execution_mode.strip():
            bound_context["execution_mode"] = execution_mode.strip().lower()
        return payload, bound_context

    async def invoke(
        self,
        function: models.Function,
        payload: dict[str, Any],
        *,
        runtime_path: str = "production",
        datasource_id: int | None = None,
        scope_metadata: dict[str, Any] | None = None,
        timeout_seconds: float = 30.0,
        run_id: str | None = None,
        trace_id: str | None = None,
    ) -> FunctionRuntimeResult:
        if function.id is None:
            raise LifecycleValidationError("Function must be persisted before runtime invocation")
        db = self._session_factory()
        function_ref = db.query(models.Function).filter(models.Function.id == function.id).first()
        if function_ref is None:
            db.close()
            raise LifecycleValidationError(f"Function {function.id} not found")

        runtime_path_normalized = str(runtime_path or "production").strip().lower()
        if runtime_path_normalized not in {"production", "draft"}:
            db.close()
            raise LifecycleValidationError("runtime_path must be production or draft")

        if runtime_path_normalized == "draft":
            code_snapshot = str(function_ref.draft_code or "").strip()
            if not code_snapshot:
                db.close()
                raise LifecycleValidationError("Function draft is empty; build the function before test run")
            runtime_release_id: int | None = None
        else:
            self._lifecycle.ensure_released_target(function_ref)
            if function_ref.current_release is None or function_ref.current_release.id is None:
                db.close()
                raise LifecycleValidationError("Function release must be persisted before runtime invocation")
            code_snapshot = function_ref.current_release.code_snapshot
            runtime_release_id = function_ref.current_release.id

        bound_payload, bound_context = self.bind_runtime_context(
            payload,
            datasource_id=datasource_id,
            scope_metadata=scope_metadata,
        )
        if trace_id:
            bound_context["trace_id"] = trace_id
        control_db_url = str(db.get_bind().url)

        now = datetime.utcnow()
        run_id_value = run_id or str(uuid.uuid4())
        logger.info(
            "function_runtime_start %s",
            fmt_kv(
                trace_id=trace_id,
                run_id=run_id_value,
                function_id=function_ref.id,
                release_id=runtime_release_id,
                runtime_path=runtime_path_normalized,
            ),
        )
        run = models.FunctionRun(
            run_id=run_id_value,
            function_id=function_ref.id,
            function_release_id=runtime_release_id,
            status=FunctionRunStatus.RUNNING.value,
            input_summary=self._summarize(bound_payload),
            runtime_context=bound_context,
            started_at=now,
            created_at=now,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        started_at = datetime.utcnow()
        try:
            output = await self._execute_in_process(
                code_snapshot,
                bound_payload,
                bound_context,
                runtime_services={"control_db_url": control_db_url},
                timeout_seconds=timeout_seconds,
            )
            finished_at = datetime.utcnow()
            duration_ms = int((finished_at - started_at).total_seconds() * 1000)

            run.status = FunctionRunStatus.SUCCESS.value
            run.duration_ms = duration_ms
            run.output_summary = self._summarize(output)
            run.finished_at = finished_at
            db.commit()
            logger.info(
                "function_runtime_success %s",
                fmt_kv(
                    trace_id=trace_id,
                    run_id=run_id_value,
                    function_id=function_ref.id,
                    release_id=runtime_release_id,
                    runtime_path=runtime_path_normalized,
                    duration_ms=duration_ms,
                ),
            )
            return FunctionRuntimeResult(
                run_id=run_id_value,
                status=FunctionRunStatus.SUCCESS.value,
                output=output,
                error_class=None,
                error_code=None,
                error_message=None,
                duration_ms=duration_ms,
            )
        except asyncio.CancelledError:
            finished_at = datetime.utcnow()
            duration_ms = int((finished_at - started_at).total_seconds() * 1000)
            run.status = FunctionRunStatus.CANCELLED.value
            run.duration_ms = duration_ms
            run.error_class = RuntimeErrorClass.CANCELLED.value
            run.error_message = "Function invocation cancelled"
            run.finished_at = finished_at
            db.commit()
            logger.warning(
                "function_runtime_cancelled %s",
                fmt_kv(
                    trace_id=trace_id,
                    run_id=run_id_value,
                    function_id=function_ref.id,
                ),
            )
            raise
        except Exception as exc:
            finished_at = datetime.utcnow()
            duration_ms = int((finished_at - started_at).total_seconds() * 1000)
            error_class = self._classify_error(exc)
            error_code = self._classify_error_code(exc, error_class)
            error_message = self._format_exception_message(exc)
            run.status = FunctionRunStatus.FAILED.value
            run.duration_ms = duration_ms
            run.error_class = error_class.value
            run.error_message = error_message
            run.finished_at = finished_at
            db.commit()
            logger.exception(
                "function_runtime_failed %s",
                fmt_kv(
                    trace_id=trace_id,
                    run_id=run_id_value,
                    function_id=function_ref.id,
                    error_class=error_class.value,
                    error_code=str(error_code or ""),
                    error=error_message,
                ),
            )
            return FunctionRuntimeResult(
                run_id=run_id_value,
                status=FunctionRunStatus.FAILED.value,
                output=None,
                error_class=error_class.value,
                error_code=error_code,
                error_message=error_message,
                duration_ms=duration_ms,
            )
        finally:
            db.close()

    async def _execute_in_process(
        self,
        code_snapshot: str,
        payload: dict[str, Any],
        context: dict[str, Any],
        runtime_services: dict[str, Any] | None,
        *,
        timeout_seconds: float,
    ) -> Any:
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            self._executor,
            _execute_code_snapshot,
            code_snapshot,
            payload,
            context,
            runtime_services,
        )
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            future.cancel()
            raise TimeoutError("Function execution timed out") from exc
        except asyncio.CancelledError:
            future.cancel()
            raise

    def _classify_error(self, exc: Exception) -> RuntimeErrorClass:
        if isinstance(exc, LifecycleValidationError):
            return RuntimeErrorClass.VALIDATION
        if isinstance(exc, TimeoutError):
            return RuntimeErrorClass.TIMEOUT
        if isinstance(exc, (ModuleNotFoundError, ImportError)):
            return RuntimeErrorClass.DEPENDENCY
        return RuntimeErrorClass.RUNTIME

    def _classify_error_code(self, exc: Exception, error_class: RuntimeErrorClass) -> str | None:
        detail = str(exc or "").strip().lower()
        if isinstance(exc, LifecycleValidationError):
            if "no released version for production path" in detail or "release must be persisted" in detail:
                return RuntimeErrorCode.RELEASE_REQUIRED.value
            return RuntimeErrorCode.VALIDATION_ERROR.value
        if isinstance(exc, RuntimeDatasourceRequiredError):
            return RuntimeErrorCode.DATASOURCE_REQUIRED.value
        if isinstance(exc, RuntimeDatasourceAccessError):
            if "sql contains ? placeholder" in detail or "sql 包含 ? 占位符" in detail:
                return RuntimeErrorCode.SQL_PARAM_PLACEHOLDER.value
            if "near '?'" in detail or "error in your sql syntax" in detail:
                return RuntimeErrorCode.SQL_SYNTAX_ERROR.value
            if "doesn't exist" in detail or "does not exist" in detail:
                return RuntimeErrorCode.SQL_OBJECT_NOT_FOUND.value
            return RuntimeErrorCode.VALIDATION_ERROR.value
        if error_class == RuntimeErrorClass.TIMEOUT:
            return RuntimeErrorCode.TIMEOUT.value
        if error_class == RuntimeErrorClass.DEPENDENCY:
            return RuntimeErrorCode.DEPENDENCY_ERROR.value
        return None

    def _format_exception_message(self, exc: Exception) -> str:
        error_type = type(exc).__name__
        detail = str(exc).strip()
        if detail:
            return f"{error_type}: {detail}"
        return error_type

    def _summarize(self, payload: Any, *, max_len: int = 600) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        if len(serialized) <= max_len:
            return serialized
        return f"{serialized[:max_len]}...<truncated>"
