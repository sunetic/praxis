# semantic-guard: allow — variables checked are HTTP response data, not direct user input
import asyncio
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.core.logging import fmt_kv, get_logger
from app.db.database import SessionLocal
from app.services.datasource.router import DataSourceRoutingError, resolve_datasource_by_role
from app.services.platform.object_tools import ObjectToolError, ObjectToolService

logger = get_logger("tools.registry")


class ToolResult:
    def __init__(self, success: bool, data: Any = None, error: Any = None):
        self.success = success
        self.data = data
        self.error = error

    def to_dict(self):
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
        }


class BaseTool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict = {}

    @abstractmethod
    async def execute(self, **params: object) -> ToolResult:
        pass

    def to_openai_function(self):
        import copy

        parameters = copy.deepcopy(self.parameters)
        props = parameters.setdefault("properties", {})
        if isinstance(props, dict) and "_runtime" not in props:
            props["_runtime"] = {
                "type": "object",
                "description": (
                    "Optional planning metadata for runtime use only; not a business parameter. "
                    "Declares the current step's phase, goal, success criteria, and whether to pause for reflection and re-planning after this step."
                ),
                "properties": {
                    "phase": {
                        "type": "string",
                        "enum": ["evidence", "verify", "action"],
                        "description": "Current step phase: evidence=gather evidence, verify=validate candidate plan, action=execute confirmed action.",
                    },
                    "goal": {
                        "type": "string",
                        "description": "The key uncertainty to resolve or short-term objective for this step.",
                    },
                    "success_criteria": {
                        "type": "string",
                        "description": "What outcome qualifies this step as truly complete, beyond just a successful tool call.",
                    },
                    "batch_boundary_after": {
                        "type": "boolean",
                        "description": "Whether to end the current batch after this tool execution and enter reflection/re-planning.",
                    },
                },
            }
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }


def _build_sql_error_payload(exc: Exception, prefix: str) -> dict[str, Any]:
    errno: int | None = None
    db_message = ""
    if isinstance(getattr(exc, "args", None), tuple):
        args = exc.args
        if len(args) >= 1 and isinstance(args[0], int):
            errno = args[0]
        if len(args) >= 2 and isinstance(args[1], str):
            db_message = args[1]
    raw_message = str(exc)
    normalized = (db_message or raw_message).lower()
    category = "execution_error"
    if errno in {1109, 1146} or "unknown table" in normalized:
        category = "unknown_table"
    elif errno in {1054} or "unknown column" in normalized:
        category = "unknown_column"
    elif errno in {1064} or "syntax error" in normalized:
        category = "syntax_error"
    elif errno in {1044, 1045, 1142, 1227} or "access denied" in normalized:
        category = "permission_error"

    retry_hint = ""
    if category in {"unknown_table", "unknown_column"}:
        retry_hint = "Discover available schema objects first, then adapt SQL."
    elif category == "permission_error":
        retry_hint = "Try switching datasource or use accessible objects."

    return {
        "code": "sql_execution_error",
        "category": category,
        "db_errno": errno,
        "db_message": db_message or raw_message,
        "message": f"{prefix}: {raw_message}",
        "retry_hint": retry_hint,
    }


class ExecuteSQLTool(BaseTool):
    name = "execute_sql"
    description = (
        "Execute SQL and return the results. "
        "For mutating SQL (INSERT/UPDATE/DELETE/DDL), a confirmation card is shown to the user "
        "and the tool returns success=false with code='pending_confirmation' — meaning the SQL has NOT been "
        "executed yet and is awaiting user approval. "
        "IMPORTANT: when you receive pending_confirmation, do NOT claim the SQL succeeded or verify results. "
        "Briefly acknowledge that the confirmation dialog has been shown and wait for the user to confirm or cancel. "
        "It is recommended to provide the intent field with a brief, user-readable description of the query purpose."
    )
    parameters = {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "The SQL statement to execute"},
            "datasource_id": {"type": "integer", "description": "Datasource ID"},
            "role": {
                "type": "string",
                "enum": ["sys", "user"],
                "description": "Connection role, defaults to user (legacy aliases tenant/business are still supported)",
            },
            "intent": {
                "type": "string",
                "description": "Purpose of this tool call (a brief, user-readable transitional note, preferably one sentence)",
            },
        },
        "required": ["sql", "datasource_id"],
    }

    async def execute(
        self, sql: str, datasource_id: int, role: str = "user", **params: object
    ) -> ToolResult:
        from app.db.database import SessionLocal
        from app.db.pool_factory import get_pool_for_datasource
        from app.models import models
        from app.services.datasource.sql_guard import (
            build_action_token,
            build_execution_fingerprint,
            is_mutating_sql,
            probe_tenant_fingerprint,
            redact_sql_preview,
        )

        db = SessionLocal()
        try:
            logger.info(
                "tool_execute_start %s",
                fmt_kv(tool=self.name, datasource_id=datasource_id, role=role),
            )
            try:
                routed = resolve_datasource_by_role(db, datasource_id, role)
            except DataSourceRoutingError as e:
                logger.warning(
                    "tool_execute_route_failed %s",
                    fmt_kv(tool=self.name, datasource_id=datasource_id, role=role),
                )
                return ToolResult(success=False, error=str(e))

            pool = get_pool_for_datasource(routed.datasource)
            is_mutating = is_mutating_sql(sql)
            if is_mutating:
                from app.api.settings import get_setting

                allow_mutating = get_setting(db, "sql_allow_mutating")
                if not allow_mutating:
                    logger.info(
                        "tool_execute_blocked_safety_mode %s",
                        fmt_kv(tool=self.name, datasource_id=datasource_id, role=role),
                    )
                    return ToolResult(
                        success=False,
                        error={
                            "code": "sql_safety_mode_active",
                            "category": "guardrail_error",
                            "message": "Write operations are disabled. Enable in Settings to allow.",
                            "retry_hint": "Change the SQL safety mode setting in Platform Settings, then retry.",
                        },
                    )

                conversation_id = params.get("conversation_id")
                batch_id = str(params.get("request_id") or "").strip()
                if not isinstance(conversation_id, int):
                    return ToolResult(
                        success=False,
                        error={
                            "code": "confirmation_context_missing",
                            "category": "guardrail_error",
                            "message": "Mutating SQL requires conversation context for confirmation.",
                            "retry_hint": "Retry from chat conversation flow with confirmation card support.",
                        },
                    )

                tenant_fingerprint = await probe_tenant_fingerprint(
                    pool,
                    routed.datasource,
                    routed.resolved_role,
                )
                if not tenant_fingerprint:
                    return ToolResult(
                        success=False,
                        error={
                            "code": "tenant_fingerprint_unavailable",
                            "category": "guardrail_error",
                            "message": "Unable to verify target tenant fingerprint before write operation.",
                            "retry_hint": "Ensure datasource has readable tenant probe functions, then retry.",
                        },
                    )

                execution_fingerprint = build_execution_fingerprint(
                    sql=sql,
                    resolved_datasource_id=routed.datasource.id,
                    resolved_role=routed.resolved_role,
                    tenant_fingerprint=tenant_fingerprint,
                )
                existing_actions = (
                    db.query(models.PendingAction)
                    .filter(
                        models.PendingAction.conversation_id == conversation_id,
                        models.PendingAction.status == "pending",
                        models.PendingAction.action_type == "execute_sql",
                    )
                    .all()
                )
                for existing in existing_actions:
                    existing_payload = existing.payload or {}
                    if (
                        str(existing_payload.get("execution_fingerprint") or "")
                        != execution_fingerprint
                    ):
                        continue
                    logger.info(
                        "tool_execute_reuse_pending_confirmation %s",
                        fmt_kv(
                            tool=self.name,
                            conversation_id=conversation_id,
                            token=existing.token,
                            resolved_datasource_id=routed.datasource.id,
                            role=routed.resolved_role,
                        ),
                    )
                    return ToolResult(
                        success=False,
                        data={
                            "requires_confirmation": True,
                            "action_type": "execute_sql",
                            "action_token": existing.token,
                            "sql_preview": existing_payload.get("sql_preview")
                            or redact_sql_preview(sql),
                            "batch_id": existing_payload.get("batch_id") or batch_id,
                            "execution_fingerprint": execution_fingerprint,
                            "tenant_fingerprint": tenant_fingerprint,
                            "resolved_datasource_id": routed.datasource.id,
                            "resolved_role": routed.resolved_role,
                            "route_reason": routed.reason,
                            "cluster_key": routed.datasource.cluster_key,
                            "deduplicated_pending_action": True,
                        },
                        error={
                            "code": "pending_confirmation",
                            "message": "SQL has NOT been executed. A confirmation dialog has been shown to the user. Do not claim success — wait for the user to confirm or cancel.",
                        },
                    )

                token = build_action_token()
                pending_payload = {
                    "sql": sql,
                    "intent": str(params.get("intent") or ""),
                    "requested_datasource_id": datasource_id,
                    "requested_role": role,
                    "resolved_datasource_id": routed.datasource.id,
                    "resolved_role": routed.resolved_role,
                    "route_reason": routed.reason,
                    "cluster_key": routed.datasource.cluster_key,
                    "batch_id": batch_id,
                    "tenant_fingerprint": tenant_fingerprint,
                    "execution_fingerprint": execution_fingerprint,
                    "sql_preview": redact_sql_preview(sql),
                }
                pending_action = models.PendingAction(
                    conversation_id=conversation_id,
                    token=token,
                    action_type="execute_sql",
                    status="pending",
                    payload=pending_payload,
                )
                db.add(pending_action)
                db.commit()
                db.refresh(pending_action)
                logger.info(
                    "tool_execute_pending_confirmation %s",
                    fmt_kv(
                        tool=self.name,
                        conversation_id=conversation_id,
                        token=token,
                        resolved_datasource_id=routed.datasource.id,
                        role=routed.resolved_role,
                    ),
                )
                return ToolResult(
                    success=False,
                    data={
                        "requires_confirmation": True,
                        "action_type": "execute_sql",
                        "action_token": token,
                        "sql_preview": pending_payload["sql_preview"],
                        "batch_id": batch_id,
                        "execution_fingerprint": execution_fingerprint,
                        "tenant_fingerprint": tenant_fingerprint,
                        "resolved_datasource_id": routed.datasource.id,
                        "resolved_role": routed.resolved_role,
                        "route_reason": routed.reason,
                        "cluster_key": routed.datasource.cluster_key,
                    },
                    error={
                        "code": "pending_confirmation",
                        "message": "SQL has NOT been executed. A confirmation dialog has been shown to the user. Do not claim success — wait for the user to confirm or cancel.",
                    },
                )

            result = await pool.execute_query(routed.datasource, sql, role=routed.resolved_role)
            result["resolved_datasource_id"] = routed.datasource.id
            result["resolved_role"] = routed.resolved_role
            result["route_reason"] = routed.reason
            result["cluster_key"] = routed.datasource.cluster_key

            logger.info(
                "tool_execute_success %s",
                fmt_kv(
                    tool=self.name,
                    datasource_id=datasource_id,
                    resolved_datasource_id=routed.datasource.id,
                    role=routed.resolved_role,
                    row_count=result.get("row_count"),
                ),
            )
            return ToolResult(success=True, data=result)
        except Exception as e:
            logger.exception(
                "tool_execute_error %s error=%s",
                fmt_kv(tool=self.name, datasource_id=datasource_id, role=role),
                str(e),
            )
            return ToolResult(
                success=False, error=_build_sql_error_payload(e, "SQL execution error")
            )
        finally:
            db.close()


class ExplainSQLTool(BaseTool):
    name = "explain_sql"
    description = "Retrieve the execution plan (EXPLAIN) for a SQL statement. It is recommended to provide the intent field with a brief, user-readable description of the analysis purpose."
    parameters = {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "The SQL statement to analyze"},
            "datasource_id": {"type": "integer", "description": "Datasource ID"},
            "role": {
                "type": "string",
                "enum": ["sys", "user"],
                "description": "Connection role, defaults to user (legacy aliases tenant/business are still supported)",
            },
            "intent": {
                "type": "string",
                "description": "Purpose of this tool call (a brief, user-readable transitional note, preferably one sentence)",
            },
        },
        "required": ["sql", "datasource_id"],
    }

    async def execute(
        self, sql: str, datasource_id: int, role: str = "user", **params: object
    ) -> ToolResult:
        from app.db.database import SessionLocal
        from app.db.pool_factory import get_pool_for_datasource

        db = SessionLocal()
        try:
            logger.info(
                "tool_execute_start %s",
                fmt_kv(tool=self.name, datasource_id=datasource_id, role=role),
            )
            try:
                routed = resolve_datasource_by_role(db, datasource_id, role)
            except DataSourceRoutingError as e:
                logger.warning(
                    "tool_execute_route_failed %s",
                    fmt_kv(tool=self.name, datasource_id=datasource_id, role=role),
                )
                return ToolResult(success=False, error=str(e))

            pool = get_pool_for_datasource(routed.datasource)
            result = await pool.execute_explain(
                routed.datasource,
                sql,
                role=routed.resolved_role,
            )
            result["resolved_datasource_id"] = routed.datasource.id
            result["resolved_role"] = routed.resolved_role
            result["route_reason"] = routed.reason
            result["cluster_key"] = routed.datasource.cluster_key

            logger.info(
                "tool_execute_success %s",
                fmt_kv(
                    tool=self.name,
                    datasource_id=datasource_id,
                    resolved_datasource_id=routed.datasource.id,
                    role=routed.resolved_role,
                ),
            )
            return ToolResult(success=True, data=result)
        except Exception as e:
            logger.exception(
                "tool_execute_error %s error=%s",
                fmt_kv(tool=self.name, datasource_id=datasource_id, role=role),
                str(e),
            )
            return ToolResult(success=False, error=_build_sql_error_payload(e, "EXPLAIN error"))
        finally:
            db.close()


class DatasourceSwitchTool(BaseTool):
    name = "datasource_switch"
    description = "Switch the datasource context for the current conversation. Subsequent queries will default to the new datasource."
    parameters = {
        "type": "object",
        "properties": {
            "datasource_id": {"type": "integer", "description": "Target datasource ID"},
            "conversation_id": {
                "type": "integer",
                "description": "Conversation ID (auto-injected)",
            },
        },
        "required": ["datasource_id"],
    }

    async def execute(
        self,
        datasource_id: int,
        conversation_id: int | None = None,
        **params: object,
    ) -> ToolResult:
        del params
        from app.models import models

        if datasource_id <= 0:
            return ToolResult(
                success=False,
                error={
                    "code": "invalid_argument",
                    "message": "datasource_id must be a positive integer",
                },
            )
        if not conversation_id:
            return ToolResult(
                success=False,
                error={"code": "missing_context", "message": "conversation_id is required"},
            )

        db = SessionLocal()
        try:
            datasource = (
                db.query(models.DataSource).filter(models.DataSource.id == datasource_id).first()
            )
            if datasource is None:
                return ToolResult(
                    success=False,
                    error={"code": "not_found", "message": f"Datasource {datasource_id} not found"},
                )
            if str(datasource.status or "").lower() != "active":
                return ToolResult(
                    success=False,
                    error={
                        "code": "inactive",
                        "message": f"Datasource {datasource_id} is not active",
                    },
                )

            conversation = (
                db.query(models.Conversation)
                .filter(models.Conversation.id == conversation_id)
                .first()
            )
            if conversation is None:
                return ToolResult(
                    success=False,
                    error={
                        "code": "not_found",
                        "message": f"Conversation {conversation_id} not found",
                    },
                )

            conversation.datasource_id = datasource.id
            db.commit()

            return ToolResult(
                success=True,
                data={
                    "message": f"Switched to datasource {datasource.name} (#{datasource.id}, {datasource.tenant_role}). Subsequent queries will use this datasource by default.",
                    "datasource_id": datasource.id,
                    "datasource_name": datasource.name,
                    "tenant_role": datasource.tenant_role,
                },
            )
        except Exception as e:
            db.rollback()
            logger.exception("datasource_switch_error datasource_id=%s", datasource_id)
            return ToolResult(success=False, error={"code": "internal_error", "message": str(e)})
        finally:
            db.close()


class AgentSaveTool(BaseTool):
    name = "agent_save"
    description = (
        "Save the current conversation as an Agent when the user explicitly requests it "
        "(e.g., 'save as agent'). The platform handles the save process automatically after invocation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "user_input": {
                "type": "string",
                "description": "User's instructions for saving the Agent",
            },
        },
    }

    async def execute(
        self,
        user_input: str = "",
        **params: object,
    ) -> ToolResult:
        del params
        return ToolResult(
            success=True,
            data={
                "action": "save_agent",
                "user_input": str(user_input or "").strip(),
            },
        )


class AgentRunTool(BaseTool):
    name = "agent_run"
    description = "Run a saved Agent in the current conversation context. This notifies the frontend to trigger the run flow."
    parameters = {
        "type": "object",
        "properties": {
            "agent_id": {"type": "integer", "description": "ID of the Agent to run"},
        },
        "required": ["agent_id"],
    }

    async def execute(
        self,
        agent_id: int,
        **params: object,
    ) -> ToolResult:
        del params
        if agent_id <= 0:
            return ToolResult(
                success=False,
                error={
                    "code": "invalid_argument",
                    "message": "agent_id must be a positive integer",
                },
            )
        from app.models import models

        db = SessionLocal()
        try:
            db_agent = db.query(models.Agent).filter(models.Agent.id == agent_id).first()
        finally:
            db.close()
        if db_agent is None:
            return ToolResult(
                success=False,
                error={"code": "agent_not_found", "message": f"Agent #{agent_id} not found"},
            )
        return ToolResult(
            success=True,
            data={
                "action": "run_agent",
                "agent_id": db_agent.id,
                "agent_name": db_agent.name,
                "agent_prompt": db_agent.prompt or "",
                "agent_tools": db_agent.tools or [],
                "agent_skills": db_agent.skills or [],
            },
        )


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self.tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self.tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        return list(self.tools.values())

    def get_openai_functions(self) -> list[dict]:
        return [tool.to_openai_function() for tool in self.list_tools()]


class ObjectCrudTool(BaseTool):
    name = "object_crud"
    description = (
        "Unified object CRUD tool. Perform create/read/update/delete/list operations on "
        "PraxisPage/PraxisFunction/Scheduler/Datasource/SchedulerHistory/Channel/KnowledgeBase/KnowledgeDocument/PraxisService."
    )
    parameters = {
        "type": "object",
        "properties": {
            "object_type": {
                "type": "string",
                "enum": [
                    "page",
                    "function",
                    "scheduler",
                    "datasource",
                    "scheduler_history",
                    "channel",
                    "knowledge_base",
                    "knowledge_document",
                    "service",
                ],
                "description": "Object type (page=PraxisPage, function=PraxisFunction, service=PraxisService)",
            },
            "action": {
                "type": "string",
                "enum": ["create", "read", "update", "delete", "list"],
                "description": "CRUD action",
            },
            "object_id": {
                "type": "integer",
                "description": "Object ID (required for read/update/delete)",
            },
            "payload": {"type": "object", "description": "Action parameters"},
            "actor": {"type": "string", "description": "Actor identifier (defaults to llm)"},
        },
        "required": ["object_type", "action"],
    }

    def __init__(self, service: ObjectToolService | None = None):
        self.service = service or ObjectToolService()

    async def execute(
        self,
        object_type: str,
        action: str,
        object_id: int | None = None,
        payload: dict[str, Any] | None = None,
        actor: str = "llm",
        **params: object,
    ) -> ToolResult:
        del params
        try:
            result = await self.service.crud(
                object_type=object_type,
                action=action,
                object_id=object_id,
                payload=payload,
                actor=actor,
            )
            return ToolResult(success=True, data=result)
        except ObjectToolError as e:
            return ToolResult(
                success=False,
                error={
                    "code": e.code,
                    "message": e.message,
                    "details": e.details or {},
                },
            )
        except Exception as e:
            logger.exception("object_crud_tool_error action=%s object_type=%s", action, object_type)
            return ToolResult(
                success=False,
                error={
                    "code": "internal_error",
                    "message": str(e),
                },
            )


class ObjectOperateTool(BaseTool):
    name = "object_operate"
    description = (
        "Unified object operation tool. Perform publish, rollback, invoke, pause, resume, send, and other actions on "
        "PraxisPage/PraxisFunction/Scheduler/Datasource/Channel."
    )
    parameters = {
        "type": "object",
        "properties": {
            "object_type": {
                "type": "string",
                "enum": ["page", "function", "scheduler", "datasource", "channel"],
                "description": "Object type (page=PraxisPage, function=PraxisFunction)",
            },
            "action": {
                "type": "string",
                "enum": [
                    "preview",
                    "publish",
                    "archive",
                    "rollback",
                    "release",
                    "strategy",
                    "verify",
                    "invoke",
                    "pause",
                    "resume",
                    "run-now",
                    "list-runs",
                    "send",
                ],
                "description": "Object action",
            },
            "object_id": {"type": "integer", "description": "Object ID"},
            "payload": {"type": "object", "description": "Action parameters"},
            "actor": {"type": "string", "description": "Actor identifier (defaults to llm)"},
        },
        "required": ["object_type", "action", "object_id"],
    }

    def __init__(self, service: ObjectToolService | None = None):
        self.service = service or ObjectToolService()

    async def execute(
        self,
        object_type: str,
        action: str,
        object_id: int,
        payload: dict[str, Any] | None = None,
        actor: str = "llm",
        **params: object,
    ) -> ToolResult:
        del params
        try:
            result = await self.service.operate(
                object_type=object_type,
                action=action,
                object_id=object_id,
                payload=payload,
                actor=actor,
            )
            return ToolResult(success=True, data=result)
        except ObjectToolError as e:
            return ToolResult(
                success=False,
                error={
                    "code": e.code,
                    "message": e.message,
                    "details": e.details or {},
                },
            )
        except Exception as e:
            logger.exception(
                "object_operate_tool_error action=%s object_type=%s", action, object_type
            )
            return ToolResult(
                success=False,
                error={
                    "code": "internal_error",
                    "message": str(e),
                },
            )


class CallServiceTool(BaseTool):
    name = "call_praxis_service"
    description = (
        "Call a registered PraxisService. Retrieves the target PraxisService's address and authentication info by service_id, "
        "sends a request to the specified path, and returns the JSON response. Suitable for registered PraxisServices such as OCP API."
    )
    parameters = {
        "type": "object",
        "properties": {
            "service_id": {
                "type": "integer",
                "description": "PraxisService ID",
            },
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE"],
                "description": "HTTP method",
            },
            "path": {
                "type": "string",
                "description": "API path (e.g. /api/v2/ob/clusters)",
            },
            "query_params": {
                "type": "object",
                "description": "URL query parameters (key-value pairs)",
            },
            "body": {
                "type": "object",
                "description": "Request body (used for POST/PUT)",
            },
        },
        "required": ["service_id", "method", "path"],
    }

    _AUTH_BUILDERS: dict[str, str] = {
        "ocp_api": "_auth_basic",
    }

    def _auth_basic(self, config: dict) -> tuple[str, str]:
        return (config.get("user", ""), config.get("password", ""))

    async def execute(
        self,
        service_id: int,
        method: str,
        path: str,
        query_params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        **params: object,
    ) -> ToolResult:
        import httpx

        from app.db.database import SessionLocal
        from app.models import models

        del params
        with SessionLocal() as db:
            svc = db.query(models.Service).filter(models.Service.id == service_id).first()
            if not svc:
                return ToolResult(
                    success=False,
                    error={"code": "not_found", "message": f"Service {service_id} not found"},
                )

            config = svc.config or {}
            base_url = f"http://{config.get('host', '')}:{config.get('port', 8080)}"
            auth_method_name = self._AUTH_BUILDERS.get(svc.service_type)

        if not auth_method_name:
            return ToolResult(
                success=False,
                error={
                    "code": "unsupported_service_type",
                    "message": f"service_type '{svc.service_type}' has no auth builder",
                },
            )

        auth_builder = getattr(self, auth_method_name)
        auth = auth_builder(config)
        url = f"{base_url}{path}"
        method_upper = method.upper()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.request(
                    method_upper,
                    url,
                    params=query_params,
                    json=body if method_upper in ("POST", "PUT") else None,
                    auth=auth,
                    headers={"Content-Type": "application/json"},
                )
                resp_data: dict[str, Any] = {}
                parsed_json = False
                try:
                    resp_data = resp.json()
                    parsed_json = True
                except Exception:
                    resp_data = {"raw_text": resp.text[:2000]}

                content_type = str((resp.headers or {}).get("content-type") or "").lower()
                raw_text = (
                    str(resp_data.get("raw_text") or "") if isinstance(resp_data, dict) else ""
                )
                raw_text_lstrip = raw_text.lstrip().lower()
                looks_like_html = raw_text_lstrip.startswith(
                    "<!doctype html"
                ) or raw_text_lstrip.startswith("<html")
                expects_json = True

                if resp.status_code >= 400:
                    return ToolResult(
                        success=False,
                        error={
                            "code": "api_error",
                            "http_status": resp.status_code,
                            "message": resp_data.get("error", {}).get("message", "")
                            if isinstance(resp_data.get("error"), dict)
                            else str(resp_data),
                            "response": resp_data,
                        },
                    )
                if expects_json and (
                    not parsed_json
                    or looks_like_html
                    or (content_type and "json" not in content_type)
                ):
                    return ToolResult(
                        success=False,
                        error={
                            "code": "unexpected_response_format",
                            "message": "Service returned non-JSON content for an API call.",
                            "content_type": content_type or None,
                            "response_preview": raw_text[:500] if raw_text else None,
                        },
                    )
        except httpx.TimeoutException:
            return ToolResult(
                success=False, error={"code": "timeout", "message": f"Request to {url} timed out"}
            )
        except Exception as e:
            return ToolResult(success=False, error={"code": "connection_error", "message": str(e)})

        return ToolResult(success=True, data=resp_data)


class ExecCommandTool(BaseTool):
    name = "exec_command"
    description = (
        "Execute a restricted shell command. Only allowlisted commands (rg/grep/sed/cat/head/tail/wc/find/ls) are permitted, "
        "and the working directory is limited to data/. Used for searching and reading knowledge base documents and other platform data files."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": ["rg", "grep", "sed", "cat", "head", "tail", "wc", "find", "ls"],
                "description": "The command to execute",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of command arguments",
            },
        },
        "required": ["command", "args"],
    }

    _ALLOWED = {"rg", "grep", "sed", "cat", "head", "tail", "wc", "find", "ls"}
    _TIMEOUT = 30
    _MAX_OUTPUT = 10000
    _RG_PATTERN_FLAGS = {"-e", "--regexp"}
    _SED_SCRIPT_FLAGS = {"-e", "--expression"}
    _SED_FORBIDDEN_FLAGS = {"-i", "-I", "--in-place", "-f", "--file"}
    _SAFE_SED_SCRIPT_PATTERNS = (
        re.compile(r"^\d+p$"),
        re.compile(r"^\d+,\d+p$"),
        re.compile(r"^\d+,\$p$"),
        re.compile(r"^\$p$"),
        re.compile(r"^/(?:\\/|[^/])+/p$"),
        re.compile(r"^/(?:\\/|[^/])+/,\s*/(?:\\/|[^/])+/p$"),
        re.compile(r"^\d+,\s*/(?:\\/|[^/])+/p$"),
        re.compile(r"^/(?:\\/|[^/])+/,\s*\d+p$"),
    )

    def _extract_path_args(self, command: str, args: list[str]) -> list[str]:
        if command == "grep":
            non_flag = [arg for arg in args if not arg.startswith("-")]
            return non_flag[1:]
        if command == "rg":
            return self._extract_rg_path_args(args)
        if command == "sed":
            return self._extract_sed_path_args(args)
        return [arg for arg in args if not arg.startswith("-")]

    def _extract_rg_path_args(self, args: list[str]) -> list[str]:
        positionals: list[str] = []
        has_explicit_pattern = False
        skip_next = False
        for arg in args:
            if skip_next:
                skip_next = False
                continue
            if arg in self._RG_PATTERN_FLAGS:
                has_explicit_pattern = True
                skip_next = True
                continue
            if arg.startswith("--regexp="):
                has_explicit_pattern = True
                continue
            if arg.startswith("-"):
                continue
            positionals.append(arg)
        if has_explicit_pattern:
            return positionals
        return positionals[1:]

    def _extract_sed_path_args(self, args: list[str]) -> list[str]:
        positionals: list[str] = []
        has_explicit_script = False
        skip_next = False
        for arg in args:
            if skip_next:
                skip_next = False
                continue
            if arg in self._SED_SCRIPT_FLAGS:
                has_explicit_script = True
                skip_next = True
                continue
            if arg.startswith("--expression="):
                has_explicit_script = True
                continue
            if arg.startswith("-"):
                continue
            positionals.append(arg)
        if has_explicit_script:
            return positionals
        return positionals[1:]

    def _extract_sed_scripts(self, args: list[str]) -> list[str]:
        scripts: list[str] = []
        positionals: list[str] = []
        skip_next = False
        for arg in args:
            if skip_next:
                skip_next = False
                scripts.append(arg)
                continue
            if arg in self._SED_SCRIPT_FLAGS:
                skip_next = True
                continue
            if arg.startswith("--expression="):
                scripts.append(arg.split("=", 1)[1])
                continue
            if arg.startswith("-"):
                continue
            positionals.append(arg)
        if scripts:
            return scripts
        return positionals[:1]

    def _validate_sed_args(self, args: list[str]) -> dict[str, Any] | None:
        for arg in args:
            if arg in self._SED_FORBIDDEN_FLAGS or any(
                arg.startswith(f"{flag}=") for flag in self._SED_FORBIDDEN_FLAGS
            ):
                return {
                    "code": "forbidden_arguments",
                    "message": f"sed flag not allowed: {arg}",
                }
        if "-n" not in args:
            return {
                "code": "invalid_arguments",
                "message": "sed requires -n and a print-only script when used via exec_command",
            }
        scripts = [
            script.strip() for script in self._extract_sed_scripts(args) if str(script).strip()
        ]
        if not scripts:
            return {
                "code": "invalid_arguments",
                "message": "sed requires a print-only script",
            }
        for script in scripts:
            if not any(pattern.fullmatch(script) for pattern in self._SAFE_SED_SCRIPT_PATTERNS):
                return {
                    "code": "forbidden_arguments",
                    "message": f"sed script not allowed: {script}",
                }
        return None

    def _validate_path_args(self, command: str, args: list[str]) -> dict[str, Any] | None:
        data_dir = Path("data").resolve()
        path_args = self._extract_path_args(command, args)
        if command in {"grep", "rg", "sed"} and not path_args:
            return {
                "code": "missing_path",
                "message": f"{command} requires at least one path under data/",
            }
        if command == "sed":
            sed_error = self._validate_sed_args(args)
            if sed_error is not None:
                return sed_error
        for arg in path_args:
            try:
                resolved = Path(arg).resolve()
            except (ValueError, OSError):
                continue
            if not str(resolved).startswith(str(data_dir)):
                return {
                    "code": "path_violation",
                    "message": f"Path must be under data/: {arg}",
                }
        return None

    async def execute(
        self, command: str, args: list[str] | None = None, **params: object
    ) -> ToolResult:
        del params
        if command not in self._ALLOWED:
            return ToolResult(
                success=False,
                error={"code": "forbidden", "message": f"Command not allowed: {command}"},
            )

        args = args or []
        path_error = self._validate_path_args(command, args)
        if path_error is not None:
            return ToolResult(success=False, error=path_error)

        try:
            proc = await asyncio.create_subprocess_exec(
                command,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path.cwd()),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._TIMEOUT)
        except TimeoutError:
            return ToolResult(
                success=False,
                error={"code": "timeout", "message": f"Command timed out after {self._TIMEOUT}s"},
            )
        except FileNotFoundError:
            return ToolResult(
                success=False,
                error={"code": "not_found", "message": f"Command not found: {command}"},
            )
        except Exception as e:
            return ToolResult(success=False, error={"code": "exec_error", "message": str(e)})

        output = stdout.decode("utf-8", errors="replace")
        if len(output) > self._MAX_OUTPUT:
            output = output[: self._MAX_OUTPUT] + f"\n... (truncated, total {len(stdout)} bytes)"

        err_text = stderr.decode("utf-8", errors="replace").strip()
        result: dict[str, Any] = {"exit_code": proc.returncode, "output": output}
        if err_text:
            result["stderr"] = err_text[:2000]

        return ToolResult(success=proc.returncode == 0, data=result)


class SkillReferenceTool(BaseTool):
    name = "skill_reference"
    description = (
        "Retrieve reference material (SQL templates, API cheat sheets, command examples, etc.) for a loaded Skill. "
        "Call this tool when you need specific SQL syntax, API paths, or operation command templates."
    )
    parameters = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "Skill name (e.g. ob-stats-ops, ocp-api-guide)",
            },
            "section": {
                "type": "string",
                "description": "Optional: keyword filter to return only paragraphs containing this keyword",
            },
        },
        "required": ["skill_name"],
    }

    async def execute(
        self, skill_name: str, section: str | None = None, **params: object
    ) -> ToolResult:
        del params
        from app.skills.store import skill_store

        skill = skill_store.get(skill_name)
        if not skill:
            return ToolResult(
                success=False,
                error={"code": "not_found", "message": f"Skill '{skill_name}' not found"},
            )
        reference = skill.reference_prompt
        if not reference:
            return ToolResult(
                success=False,
                error={
                    "code": "no_reference",
                    "message": f"Skill '{skill_name}' has no reference material",
                },
            )
        if section:
            section_lower = section.lower()
            paragraphs = reference.split("\n\n")
            matched = [p for p in paragraphs if section_lower in p.lower()]
            if matched:
                reference = "\n\n".join(matched)
        return ToolResult(success=True, data={"skill_name": skill_name, "reference": reference})


class RenderChartTool(BaseTool):
    name = "render_chart"
    description = (
        "Render an interactive chart in the conversation. "
        "Pass a valid ECharts option JSON object to generate a chart visualization. "
        "Use this after querying data when results suit visual presentation "
        "(trends, comparisons, distributions, etc.). "
        "The option must be a valid ECharts option containing fields like xAxis, yAxis, series, etc."
    )
    parameters = {
        "type": "object",
        "properties": {
            "option": {
                "type": "object",
                "description": (
                    "ECharts option configuration object (JSON). "
                    "Contains chart type, data, axes, and other configuration."
                ),
            },
            "title": {
                "type": "string",
                "description": "Optional chart title displayed above the chart.",
            },
        },
        "required": ["option"],
    }

    async def execute(self, option: object = None, title: str = "", **params: object) -> ToolResult:
        del params
        if not isinstance(option, dict):
            return ToolResult(
                success=False,
                error={"code": "invalid_option", "message": "option must be a JSON object"},
            )
        data: dict = {"option": option}
        if title:
            data["title"] = title
        return ToolResult(success=True, data=data)


class KnowledgeSearchTool(BaseTool):
    name = "knowledge_search"
    description = (
        "Search the platform knowledge base for relevant documentation. "
        "An autonomous search agent will find, read, and synthesize information from knowledge base documents. "
        "Returns structured results with snippets and a summary."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query in natural language (Chinese or English)",
            },
            "db_type": {
                "type": "string",
                "description": "Database type to search docs for (e.g. mysql, postgresql, oceanbase). Matches knowledge packs by database type.",
            },
            "version": {
                "type": "string",
                "description": "Database version (e.g. '8.0', '8.4'). If omitted, uses the currently installed version.",
            },
            "kb_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Optional list of knowledge base IDs to search. If omitted and db_type is provided, auto-selects the matching pack.",
            },
        },
        "required": ["query"],
    }

    async def execute(
        self,
        query: str = "",
        db_type: str | None = None,
        version: str | None = None,
        kb_ids: list[int] | None = None,
        **params: object,
    ) -> ToolResult:
        del params
        if not query.strip():
            return ToolResult(
                success=False, error={"code": "invalid_argument", "message": "query is required"}
            )
        from app.services.knowledge.search_agent import KnowledgeSearchAgent

        agent = KnowledgeSearchAgent()
        try:
            result = await agent.run(query=query, kb_ids=kb_ids, db_type=db_type, version=version)
            return ToolResult(success=True, data=result)
        except Exception as e:
            return ToolResult(success=False, error={"code": "search_error", "message": str(e)})


registry = ToolRegistry()
registry.register(ExecuteSQLTool())
registry.register(ExplainSQLTool())
registry.register(ObjectCrudTool())
registry.register(ObjectOperateTool())
registry.register(CallServiceTool())
registry.register(ExecCommandTool())
registry.register(DatasourceSwitchTool())
registry.register(AgentSaveTool())
registry.register(AgentRunTool())
registry.register(SkillReferenceTool())
registry.register(KnowledgeSearchTool())
registry.register(RenderChartTool())
