from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.models import models
from app.services.agent.coding_events import (
    coding_event_summary,
    emit_coding_event,
    sanitize_coding_event_data,
)
from app.services.agent.core import AgentCore, BuildGoalRequest, normalize_build_attempts
from app.services.agent.reasoning_engine import SimpleToolExecutor
from app.services.chat import ChatService
from app.services.chat.agent import ChatCoreAgent
from app.services.function.authoring_agent import (
    FunctionAuthoringAgent,
    FunctionInvokeCommand,
    FunctionSuggestInputCommand,
)
from app.services.function.coding_tools import FunctionCodingTools
from app.services.function.runtime import FunctionRuntimeResult
from app.services.function.scope_adapter import FunctionBuildScopeAdapter
from app.services.platform.coding_engine import CodingEngineApplyResult
from app.services.platform.prompt_loader import PromptLoader
from app.services.platform.workspace_store import WorkspaceStore


class FunctionChatAgent(ChatCoreAgent):
    """
    System Agent for Function domain — build, invoke, suggest input.
    """

    key = "function_build"
    display_name = "FunctionChatAgent"

    async def run_coding_task(
        self,
        *,
        goal: str,
        workspace_dir: Path,
        purpose: str = "implement",
        build_context: dict[str, Any] | None = None,
        max_iterations: int = 10,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> CodingEngineApplyResult:
        """Run a Function coding task through the shared Chat/Reasoning runtime."""
        normalized_purpose = str(purpose or "implement").strip().lower()
        if normalized_purpose not in {"analyze", "implement"}:
            raise ValueError(f"Unsupported Function coding purpose: {purpose}")

        tools_runtime = FunctionCodingTools(workspace_dir=workspace_dir)
        tool_schemas = tools_runtime.schemas() if normalized_purpose == "implement" else None
        tool_executor = (
            SimpleToolExecutor(tools_runtime.execute) if normalized_purpose == "implement" else None
        )
        system_prompt = PromptLoader.render(
            "function/prompts/chat_build.tpl",
            purpose=normalized_purpose,
            build_context_json=json.dumps(build_context or {}, ensure_ascii=False, default=str),
        )
        self._chat_service.max_iterations = max(1, int(max_iterations or 1))

        assistant_by_iteration: dict[int, str] = {}
        async for event in self.stream_general_chat(
            messages=[{"role": "user", "content": str(goal or "").strip()}],
            tools=tool_schemas,
            system_prompt=system_prompt,
            default_datasource_id=(build_context or {}).get("datasource_id"),
            conversation_id=None,
            scope_context=None,
            tool_executor=tool_executor,
        ):
            meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
            iteration = int(meta.get("iteration") or 0)
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            if event.get("type") == "assistant":
                text = str(data.get("text") or "")
                if text:
                    assistant_by_iteration[iteration] = (
                        assistant_by_iteration.get(iteration, "") + text
                    )
            self._emit_coding_event(event_callback, event)

        final_text = ""
        for iteration in sorted(assistant_by_iteration, reverse=True):
            candidate = assistant_by_iteration[iteration].strip()
            if candidate:
                final_text = candidate
                break

        if normalized_purpose == "analyze":
            parsed = self._parse_json_object(final_text)
            result_status = str(parsed.get("result_status") or "completed").strip().lower()
            if result_status not in {"clear", "refined", "needs_clarification", "too_complex"}:
                result_status = "completed"
            return CodingEngineApplyResult(
                changed_files=[],
                diff_summary="",
                tests_suggested=[],
                risk_notes=[],
                assistant_message=str(parsed.get("result") or final_text).strip(),
                result_status=result_status,
            )

        completion = tools_runtime.state.completion
        if completion is None:
            parsed = self._parse_json_object(final_text)
            fallback_status = (
                str(parsed.get("outcome") or parsed.get("result_status") or "completed")
                .strip()
                .lower()
            )
            if fallback_status == "completed" and (
                tools_runtime.state.probe_required
                or tools_runtime.state.verified_revision != tools_runtime.current_revision()
            ):
                raise ValueError(
                    "Function agent stopped without verifying the current main.py revision"
                )
            completion = {
                "outcome": fallback_status,
                "assistant_message": str(
                    parsed.get("assistant_message") or parsed.get("result") or final_text
                ).strip(),
                "diff_summary": str(parsed.get("diff_summary") or "").strip(),
                "tests_suggested": parsed.get("tests_suggested") or [],
                "risk_notes": parsed.get("risk_notes") or [],
            }

        outcome = str(completion.get("outcome") or "completed").strip().lower()
        if outcome not in {"completed", "needs_clarification", "too_complex"}:
            outcome = "completed"
        return CodingEngineApplyResult(
            changed_files=sorted(tools_runtime.state.changed_files),
            diff_summary=str(
                completion.get("diff_summary")
                or f"Applied {len(tools_runtime.state.changed_files)} Function source change(s)"
            ),
            tests_suggested=self._normalize_string_list(completion.get("tests_suggested")),
            risk_notes=self._normalize_string_list(completion.get("risk_notes")),
            assistant_message=str(
                completion.get("assistant_message") or "Function build completed."
            ),
            result_status=outcome,
        )

    @staticmethod
    def _emit_coding_event(
        callback: Callable[[dict[str, Any]], None] | None,
        event: dict[str, Any],
    ) -> None:
        emit_coding_event(callback, event, domain_label="Function")

    @staticmethod
    def _sanitize_coding_event_data(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        return sanitize_coding_event_data(event_type, data, domain_label="Function")

    @staticmethod
    def _coding_event_summary(event_type: str, data: dict[str, Any]) -> str:
        return coding_event_summary(event_type, data, domain_label="Function")

    @staticmethod
    def _parse_json_object(raw: str) -> dict[str, Any]:
        text = str(raw or "").strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        if not text:
            raise ValueError("Function agent returned no structured result")
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("Function agent result must be a JSON object")
        return parsed

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def __init__(
        self,
        *,
        function_authoring_agent: FunctionAuthoringAgent | None = None,
        chat_service: ChatService | None = None,
        agent_core: AgentCore | None = None,
        function_scope_adapter: FunctionBuildScopeAdapter | None = None,
    ) -> None:
        super().__init__(chat_service=chat_service)
        self._agent_core = agent_core or AgentCore()
        self._function_authoring_agent = function_authoring_agent or FunctionAuthoringAgent()
        self._function_scope_adapter = function_scope_adapter or FunctionBuildScopeAdapter()

    def compose_function_build_goal(
        self,
        *,
        prompt: str,
        recent_contexts: list[dict[str, Any]] | None = None,
        conversation_context: str = "",
        skill_context: str = "",
    ) -> str:
        return self._agent_core.compose_build_goal(
            adapter=self._function_scope_adapter,
            request=BuildGoalRequest(
                user_prompt=prompt,
                recent_contexts=normalize_build_attempts(recent_contexts),
                conversation_context=conversation_context,
                skill_context=skill_context,
            ),
        )

    def apply_function_goal(
        self,
        *,
        function: models.Function,
        goal: str,
        workspace_store: WorkspaceStore | None = None,
        datasource_schema: dict[str, Any] | None = None,
        datasource_id: int | None = None,
    ) -> CodingEngineApplyResult:
        store = workspace_store or WorkspaceStore()
        return self._function_scope_adapter.apply_goal(
            workspace_store=store,
            target=function,
            goal=goal,
            datasource_schema=datasource_schema,
            datasource_id=datasource_id,
        )

    def suggest_function_input(
        self,
        *,
        function: models.Function,
        prompt: str,
        conversation_context: str,
    ) -> dict[str, Any]:
        return self._function_authoring_agent.suggest_input(
            function=function,
            command=FunctionSuggestInputCommand(
                prompt=prompt,
                conversation_context=conversation_context,
            ),
        )

    async def invoke_function(
        self,
        *,
        function: models.Function,
        runtime_session_factory: sessionmaker[Session],
        command: FunctionInvokeCommand,
    ) -> FunctionRuntimeResult:
        return await self._function_authoring_agent.invoke(
            function=function,
            runtime_session_factory=runtime_session_factory,
            command=command,
        )
