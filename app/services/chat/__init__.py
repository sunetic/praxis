"""
Chat service with explicit state machine and tool lifecycle.
"""

import json
import uuid
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace

from app.core.logging import fmt_kv, get_logger
from app.services.agent.reasoning_engine import (
    VALID_TRANSITIONS as ENGINE_VALID_TRANSITIONS,
)
from app.services.agent.reasoning_engine import (
    EngineConfig,
    ReasoningEngine,
    ReasoningPhase,
)
from app.services.llm import get_llm_client
from app.tools.registry import registry

tracer = trace.get_tracer("app.services.chat")

logger = get_logger("chat.service")

__all__ = ["ChatPhase", "ChatService", "get_chat_service", "get_llm_client"]


ChatPhase = ReasoningPhase


class _ChatToolExecutor:
    def __init__(
        self,
        service: "ChatService",
        *,
        default_datasource_id: int | None,
        scope_context: dict[str, Any] | None,
    ) -> None:
        self._service = service
        self._default_datasource_id = default_datasource_id
        self._scope_context = scope_context

    def preview_tool_start(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        return self._service._preview_tool_start(tool_call, self._default_datasource_id)

    async def execute_tool(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        return await self._service._execute_tool_lifecycle(
            tool_call=tool_call,
            default_datasource_id=self._default_datasource_id,
            scope_context=self._scope_context,
        )


class ChatService:
    """
    Handles chat with tool calling support.
    """

    VALID_TRANSITIONS: dict[ChatPhase, set[ChatPhase]] = ENGINE_VALID_TRANSITIONS

    def __init__(self, max_iterations: int = 50, max_reflections: int = 2, llm: Any | None = None):
        self.max_iterations = max_iterations
        self.max_reflections = max_reflections
        self.max_progress_bonus_iterations = 8
        self.max_repeated_tool_rounds = 2
        self._active_scope_context: dict[str, Any] = {}
        self._active_conversation_id: int | None = None
        self._active_request_id: str | None = None
        self._active_agent_name: str = ""
        self.llm = llm or get_llm_client()

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        default_datasource_id: int | None = None,
        conversation_id: int | None = None,
        scope_context: dict[str, Any] | None = None,
        use_state_machine: bool | None = None,
        agent_name: str = "",
        is_cancelled: Callable[[], bool] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        del use_state_machine
        chat_messages = list(messages)
        if system_prompt and not any(m.get("role") == "system" for m in chat_messages):
            chat_messages.insert(0, {"role": "system", "content": system_prompt})
        self._active_scope_context = scope_context or {}
        self._active_conversation_id = conversation_id
        self._active_agent_name = agent_name

        request_id = str(uuid.uuid4())
        self._active_request_id = request_id

        _span = tracer.start_span(
            "chat.session",
            attributes={
                "chat.conversation_id": conversation_id or 0,
                "chat.request_id": request_id,
                "chat.message_count": len(chat_messages),
                "chat.has_tools": bool(tools),
            },
        )
        _ctx = trace.set_span_in_context(_span)
        _token = otel_context.attach(_ctx)

        phase = ChatPhase.THINKING
        iteration = 0
        final_phase = ChatPhase.THINKING

        logger.info(
            "chat_flow_start %s",
            fmt_kv(
                request_id=request_id,
                conversation_id=conversation_id,
                message_count=len(chat_messages),
            ),
        )
        engine = ReasoningEngine(
            config=EngineConfig(
                max_iterations=self.max_iterations,
                max_reflections=self.max_reflections,
                max_progress_bonus=self.max_progress_bonus_iterations,
                max_repeated_tool_rounds=self.max_repeated_tool_rounds,
            ),
            llm=self.llm,
            tool_executor=_ChatToolExecutor(
                self,
                default_datasource_id=default_datasource_id,
                scope_context=scope_context,
            ),
            tool_plan_extractor=self._extract_object_action_plan,
        )
        try:
            async for raw_event in engine.run(
                messages=messages,
                tools=tools,
                system_prompt=system_prompt,
                is_cancelled=is_cancelled,
            ):
                raw_phase = ChatPhase(raw_event["phase"])
                final_phase = raw_phase
                meta = dict(raw_event.get("meta") or {})
                iteration = max(iteration, int(meta.get("iteration") or 0))
                yield self._event(
                    type_=str(raw_event["type"]),
                    phase=raw_phase,
                    data=raw_event.get("data"),
                    request_id=request_id,
                    conversation_id=conversation_id,
                    meta=meta,
                )
        except Exception as exc:
            logger.exception(
                "chat_engine_bridge_error %s error=%s",
                fmt_kv(request_id=request_id, conversation_id=conversation_id, iteration=iteration),
                str(exc),
            )
            final_phase = ChatPhase.ERROR
            yield self._error_event(
                request_id=request_id,
                conversation_id=conversation_id,
                phase=phase,
                message=str(exc),
                iteration=iteration,
                error_class="runtime_error",
            )
        finally:
            _span.set_attribute("chat.iterations", iteration)
            _span.set_attribute("chat.final_phase", str(final_phase))
            if final_phase == ChatPhase.ERROR:
                _span.set_status(trace.StatusCode.ERROR, "chat ended in error phase")
            otel_context.detach(_token)
            _span.end()

    def _preview_tool_start(
        self,
        tool_call: dict,
        default_datasource_id: int | None,
    ) -> dict[str, str]:
        normalized = self._normalize_tool_call(tool_call)
        if not normalized["ok"]:
            return {
                "tool_call_id": normalized["tool_call_id"],
                "name": normalized["name"],
                "arguments": normalized["arguments_text"],
            }
        bound_args = self._bind_tool_context(
            normalized["name"],
            normalized["arguments"],
            default_datasource_id,
        )
        return {
            "tool_call_id": normalized["tool_call_id"],
            "name": normalized["name"],
            "arguments": json.dumps(
                self._sanitize_event_arguments(bound_args),
                ensure_ascii=False,
                default=str,
            ),
        }

    async def _execute_tool_lifecycle(
        self,
        tool_call: dict,
        default_datasource_id: int | None,
        scope_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        normalized = self._normalize_tool_call(tool_call)
        if not normalized["ok"]:
            return {
                "tool_call_id": normalized["tool_call_id"],
                "name": normalized["name"],
                "arguments": normalized["arguments_text"],
                "result": {"success": False, "error": normalized["error"]},
                "error_class": "argument_error",
            }

        bound_args = self._bind_tool_context(
            normalized["name"],
            normalized["arguments"],
            default_datasource_id,
        )
        bound_arguments_text = json.dumps(
            self._sanitize_event_arguments(bound_args),
            ensure_ascii=False,
            default=str,
        )
        scope_check_error = self._check_scope_policy(
            normalized["name"],
            bound_args,
            scope_context=scope_context,
        )
        if scope_check_error:
            return {
                "tool_call_id": normalized["tool_call_id"],
                "name": normalized["name"],
                "arguments": bound_arguments_text,
                "result": {
                    "success": False,
                    "error": {
                        "code": "scope_violation",
                        "message": scope_check_error,
                    },
                },
                "error_class": "scope_violation",
            }
        if normalized["name"] == "call_praxis_service" and "service_id" not in bound_args:
            _, binding_error = self._resolve_call_service_binding(default_datasource_id)
            return {
                "tool_call_id": normalized["tool_call_id"],
                "name": normalized["name"],
                "arguments": bound_arguments_text,
                "result": {
                    "success": False,
                    "error": {
                        "code": "missing_service_binding",
                        "message": binding_error
                        or "Cannot auto-determine PraxisService; please provide service_id explicitly.",
                    },
                },
                "error_class": "dependency_error",
            }
        tool = registry.get(normalized["name"])
        if not tool:
            error_payload: dict[str, Any] | str
            if "." in str(normalized["name"] or ""):
                error_payload = {
                    "code": "capability_not_tool",
                    "message": f"Capability '{normalized['name']}' is not exposed as a tool",
                }
            else:
                error_payload = f"Tool '{normalized['name']}' not found"
            return {
                "tool_call_id": normalized["tool_call_id"],
                "name": normalized["name"],
                "arguments": bound_arguments_text,
                "result": {"success": False, "error": error_payload},
                "error_class": "dependency_error",
            }

        tool_name = normalized["name"] or "unknown"
        with tracer.start_as_current_span(
            f"chat.tool.{tool_name}",
            attributes={
                "chat.tool.name": tool_name,
                "chat.tool.call_id": normalized["tool_call_id"] or "",
            },
        ) as tool_span:
            try:
                result = await tool.execute(**bound_args)
                payload = result.to_dict()
                tool_span.set_attribute("chat.tool.success", payload.get("success", False))
                execution_ids = self._extract_execution_ids(bound_args, payload)
                runtime_meta = (
                    bound_args.get("_runtime")
                    if isinstance(bound_args.get("_runtime"), dict)
                    else {}
                )
                planning_meta = {
                    "phase": str(runtime_meta.get("phase") or "").strip(),
                    "goal": str(runtime_meta.get("goal") or "").strip(),
                    "success_criteria": str(runtime_meta.get("success_criteria") or "").strip(),
                }
                return {
                    "tool_call_id": normalized["tool_call_id"],
                    "name": normalized["name"],
                    "arguments": bound_arguments_text,
                    "result": payload,
                    "error_class": self._classify_tool_result(payload),
                    "run_id": execution_ids.get("run_id"),
                    "object_id": execution_ids.get("object_id"),
                    "release_id": execution_ids.get("release_id"),
                    "batch_boundary_after": bool(runtime_meta.get("batch_boundary_after")),
                    "planning_meta": planning_meta,
                }
            except TimeoutError as e:
                tool_span.record_exception(e)
                tool_span.set_status(trace.StatusCode.ERROR, str(e))
                return {
                    "tool_call_id": normalized["tool_call_id"],
                    "name": normalized["name"],
                    "arguments": bound_arguments_text,
                    "result": {"success": False, "error": str(e)},
                    "error_class": "timeout_error",
                }
            except Exception as e:
                tool_span.record_exception(e)
                tool_span.set_status(trace.StatusCode.ERROR, str(e))
                logger.exception("tool_lifecycle_execute_error %s", fmt_kv(tool=normalized["name"]))
                return {
                    "tool_call_id": normalized["tool_call_id"],
                    "name": normalized["name"],
                    "arguments": bound_arguments_text,
                    "result": {"success": False, "error": str(e)},
                    "error_class": "execution_error",
                }

    def _normalize_tool_call(self, tool_call: dict) -> dict[str, Any]:
        function = tool_call.get("function") if isinstance(tool_call, dict) else {}
        function = function if isinstance(function, dict) else {}
        name = function.get("name", "")
        arguments_text = function.get("arguments", "")
        tool_call_id = tool_call.get("id") or str(uuid.uuid4())

        if not name:
            return {
                "ok": False,
                "tool_call_id": tool_call_id,
                "name": "",
                "arguments": {},
                "arguments_text": arguments_text if isinstance(arguments_text, str) else "{}",
                "error": "Tool call missing function name",
            }

        if not isinstance(arguments_text, str):
            arguments_text = json.dumps(arguments_text, ensure_ascii=False)

        try:
            arguments = json.loads(arguments_text or "{}")
            if not isinstance(arguments, dict):
                return {
                    "ok": False,
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "arguments": {},
                    "arguments_text": arguments_text,
                    "error": "Tool arguments must be a JSON object",
                }
        except json.JSONDecodeError as e:
            return {
                "ok": False,
                "tool_call_id": tool_call_id,
                "name": name,
                "arguments": {},
                "arguments_text": arguments_text,
                "error": f"Invalid tool arguments: {str(e)}",
            }

        return {
            "ok": True,
            "tool_call_id": tool_call_id,
            "name": name,
            "arguments": arguments,
            "arguments_text": arguments_text,
            "error": None,
        }

    def _bind_tool_context(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        default_datasource_id: int | None,
    ) -> dict[str, Any]:
        bound = dict(arguments)
        runtime_meta = bound.pop("_runtime", None)
        if (
            default_datasource_id is not None
            and "datasource_id" not in bound
            and tool_name in {"execute_sql", "explain_sql"}
        ):
            bound["datasource_id"] = default_datasource_id
        if (
            self._active_conversation_id is not None
            and "conversation_id" not in bound
            and tool_name in {"execute_sql", "explain_sql", "datasource_switch", "agent_save"}
        ):
            bound["conversation_id"] = self._active_conversation_id
        if (
            self._active_request_id is not None
            and "request_id" not in bound
            and tool_name in {"execute_sql", "explain_sql"}
        ):
            bound["request_id"] = self._active_request_id
        if isinstance(runtime_meta, dict) and runtime_meta:
            bound["_runtime"] = runtime_meta
        if (
            self._active_scope_context
            and tool_name in {"object_crud", "object_operate"}
            and "payload" not in bound
        ):
            bound["payload"] = {}
        if tool_name == "call_praxis_service" and "service_id" not in bound:
            service_id, _ = self._resolve_call_service_binding(default_datasource_id)
            if service_id is not None:
                bound["service_id"] = service_id
        return bound

    def _resolve_call_service_binding(
        self, default_datasource_id: int | None
    ) -> tuple[int | None, str | None]:
        if default_datasource_id is None:
            return (
                None,
                "No datasource is bound to the current session; cannot auto-resolve PraxisService.",
            )

        from app.db.database import SessionLocal
        from app.models import models

        with SessionLocal() as db:
            datasource = (
                db.query(models.DataSource)
                .filter(models.DataSource.id == default_datasource_id)
                .first()
            )
            if not datasource:
                return (
                    None,
                    f"Datasource {default_datasource_id} not found; cannot auto-resolve PraxisService.",
                )
            if not datasource.cluster_key:
                return (
                    None,
                    "The current datasource is missing cluster_key; cannot auto-resolve PraxisService.",
                )

            services = (
                db.query(models.Service)
                .filter(
                    models.Service.resource_ref == f"cluster:{datasource.cluster_key}",
                    models.Service.status == "active",
                )
                .order_by(models.Service.id.asc())
                .all()
            )
            if len(services) == 1:
                return int(services[0].id), None
            if not services:
                return (
                    None,
                    f"No active PraxisService is bound to the current datasource cluster_key={datasource.cluster_key}.",
                )
            service_ids = ", ".join(str(item.id) for item in services[:5])
            return (
                None,
                "Multiple active PraxisServices are associated with the current datasource; cannot auto-determine service_id: "
                f"{service_ids}. Please specify service_id explicitly.",
            )

    def _sanitize_event_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(arguments)
        sanitized.pop("conversation_id", None)
        sanitized.pop("request_id", None)
        sanitized.pop("_runtime", None)
        return sanitized

    def _check_scope_policy(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        scope_context: dict[str, Any] | None,
    ) -> str | None:
        if not scope_context:
            return None
        scope_type = scope_context.get("scope_type")
        if scope_type != "builder":
            return None

        allowed_tools = {"object_crud", "object_operate", "execute_sql", "explain_sql"}
        if tool_name not in allowed_tools:
            return f"Tool '{tool_name}' is not allowed in builder scope"

        if tool_name not in {"object_crud", "object_operate"}:
            return None

        object_type = str(arguments.get("object_type") or "").strip().lower()
        action = str(arguments.get("action") or "").strip().lower()
        scope_object_type = str(scope_context.get("scope_object_type") or "").strip().lower()
        scope_object_id = str(scope_context.get("scope_object_id") or "").strip()
        target_object_id = arguments.get("object_id")
        target_object_id_text = str(target_object_id) if target_object_id is not None else ""

        if not object_type:
            return "object_type is required in builder scope"

        if object_type == scope_object_type:
            if action in {"list"}:
                return "List operation is blocked in builder scope"
            if action == "create":
                return f"Create {object_type} is out of scope for current builder object"
            if (
                target_object_id_text
                and scope_object_id
                and target_object_id_text != scope_object_id
            ):
                return (
                    f"Target {object_type}:{target_object_id_text} is outside builder scope "
                    f"{scope_object_type}:{scope_object_id}"
                )
            if not target_object_id_text and tool_name == "object_operate":
                return "object_id is required for scoped operate action"
            return None

        if scope_object_type == "page" and object_type in {"function", "scheduler"}:
            return None

        return (
            f"Target object type '{object_type}' is not permitted in builder scope "
            f"for {scope_object_type}:{scope_object_id}"
        )

    def _classify_tool_result(self, payload: dict[str, Any]) -> str:
        if payload.get("success"):
            return "none"
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            error_text = " ".join(
                [
                    str(error_payload.get("code") or ""),
                    str(error_payload.get("message") or ""),
                ]
            ).lower()
        else:
            error_text = str(error_payload or "").lower()
        if "timeout" in error_text:
            return "timeout_error"
        if "scope_violation" in error_text or "out of scope" in error_text:
            return "scope_violation"
        if "unknown table" in error_text or "unknown column" in error_text:
            return "schema_error"
        if "permission" in error_text or "access denied" in error_text:
            return "permission_error"
        if "invalid" in error_text or "json" in error_text:
            return "argument_error"
        if "not found" in error_text:
            return "dependency_error"
        return "execution_error"

    def _extract_object_action_plan(self, tool_calls: dict[int, dict]) -> list[dict[str, Any]]:
        plan: list[dict[str, Any]] = []
        for _, call in sorted(tool_calls.items(), key=lambda item: item[0]):
            function = call.get("function") if isinstance(call, dict) else {}
            function = function if isinstance(function, dict) else {}
            name = str(function.get("name") or "")
            args_text = function.get("arguments") or "{}"
            if name not in {"object_crud", "object_operate"}:
                continue
            if not isinstance(args_text, str):
                args_text = json.dumps(args_text, ensure_ascii=False)
            try:
                args = json.loads(args_text)
            except Exception:
                args = {}
            plan.append(
                {
                    "tool": name,
                    "object_type": args.get("object_type"),
                    "action": args.get("action"),
                    "object_id": args.get("object_id"),
                }
            )
        return plan

    def _extract_execution_ids(
        self,
        bound_args: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        ids: dict[str, Any] = {}
        if isinstance(bound_args.get("object_id"), (int, str)):
            ids["object_id"] = str(bound_args.get("object_id"))
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                if isinstance(data.get("id"), (int, str)) and "object_id" not in ids:
                    ids["object_id"] = str(data.get("id"))
                if isinstance(data.get("run_id"), str):
                    ids["run_id"] = data.get("run_id")
                if isinstance(data.get("current_release_id"), (int, str)):
                    ids["release_id"] = str(data.get("current_release_id"))
                release = data.get("release")
                if isinstance(release, dict) and isinstance(release.get("id"), (int, str)):
                    ids["release_id"] = str(release.get("id"))
        return ids

    def _event(
        self,
        type_: str,
        phase: ChatPhase,
        data: Any,
        request_id: str,
        conversation_id: int | None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "type": type_,
            "id": str(uuid.uuid4()),
            "ts": datetime.now(UTC).isoformat(),
            "phase": phase.value,
            "data": data,
            "meta": {
                "request_id": request_id,
                "trace_id": request_id,
                "conversation_id": conversation_id,
            },
        }
        if self._active_agent_name:
            payload["data"] = {
                **(data if isinstance(data, dict) else {"value": data}),
                "agent_name": self._active_agent_name,
            }
        if self._active_scope_context:
            payload["meta"].update(
                {
                    "scope_type": self._active_scope_context.get("scope_type"),
                    "scope_object_type": self._active_scope_context.get("scope_object_type"),
                    "scope_object_id": self._active_scope_context.get("scope_object_id"),
                    "build_session_id": self._active_scope_context.get("build_session_id"),
                }
            )
        if meta:
            payload["meta"].update(meta)
        return payload

    def _error_event(
        self,
        request_id: str,
        conversation_id: int | None,
        phase: ChatPhase,
        message: str,
        iteration: int,
        error_class: str,
    ) -> dict[str, Any]:
        return self._event(
            type_="error",
            phase=ChatPhase.ERROR if phase != ChatPhase.DONE else phase,
            data={"message": message, "error_class": error_class},
            request_id=request_id,
            conversation_id=conversation_id,
            meta={"iteration": iteration, "error_class": error_class},
        )


def get_chat_service() -> ChatService:
    """Get or create chat service instance."""
    return ChatService()
