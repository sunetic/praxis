from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.models import models
from app.services.agent.coding_events import emit_coding_event
from app.services.agent.core import AgentCore, BuildGoalRequest, normalize_build_attempts
from app.services.agent.reasoning_engine import SimpleToolExecutor
from app.services.chat import ChatService
from app.services.chat.agent import ChatCoreAgent
from app.services.page.coding_tools import PageCodingTools
from app.services.page.scope_adapter import PageBuildScopeAdapter
from app.services.platform.coding_engine import CodingEngineApplyResult
from app.services.platform.prompt_loader import PromptLoader
from app.services.platform.workspace_store import WorkspaceStore


class PageChatAgent(ChatCoreAgent):
    """
    System Agent for Page domain build conversations.

    Phase 1 keeps the page build workflow kernel in PageBuilderOrchestrator /
    BuildVerificationPipeline and extracts only the page-scoped chat facade so build
    planning/apply logic no longer bypasses the System Agent layer entirely.
    """

    key = "page_build"
    display_name = "PageChatAgent"

    async def run_coding_task(
        self,
        *,
        goal: str,
        workspace_dir: Path,
        build_context: dict[str, Any] | None = None,
        max_iterations: int = 10,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> CodingEngineApplyResult:
        """Run Page authoring through the shared Chat/Reasoning runtime."""
        tools_runtime = PageCodingTools(workspace_dir=workspace_dir)
        self._chat_service.max_iterations = max(1, int(max_iterations or 1))
        assistant_by_iteration: dict[int, str] = {}

        async for event in self.stream_general_chat(
            messages=[{"role": "user", "content": str(goal or "").strip()}],
            tools=tools_runtime.schemas(),
            system_prompt=PromptLoader.render(
                "page/prompts/chat_build.tpl",
                build_context_json=json.dumps(build_context or {}, ensure_ascii=False, default=str),
            ),
            default_datasource_id=None,
            conversation_id=None,
            scope_context=None,
            tool_executor=SimpleToolExecutor(tools_runtime.execute),
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
            emit_coding_event(event_callback, event, domain_label="Page")

        final_text = ""
        for iteration in sorted(assistant_by_iteration, reverse=True):
            candidate = assistant_by_iteration[iteration].strip()
            if candidate:
                final_text = candidate
                break

        completion = tools_runtime.state.completion
        if completion is None:
            parsed = self._parse_json_object(final_text)
            fallback_status = (
                str(parsed.get("outcome") or parsed.get("result_status") or "completed")
                .strip()
                .lower()
            )
            if fallback_status == "completed" and (
                tools_runtime.state.check_required
                or tools_runtime.state.verified_revisions != tools_runtime.current_revisions()
            ):
                raise ValueError(
                    "Page agent stopped without verifying the current source revisions"
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
                or f"Applied {len(tools_runtime.state.changed_files)} Page source change(s)"
            ),
            tests_suggested=self._normalize_string_list(completion.get("tests_suggested")),
            risk_notes=self._normalize_string_list(completion.get("risk_notes")),
            assistant_message=str(completion.get("assistant_message") or "Page build completed."),
            result_status=outcome,
        )

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
            raise ValueError("Page agent returned no structured result")
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("Page agent result must be a JSON object")
        return parsed

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def __init__(
        self,
        *,
        chat_service: ChatService | None = None,
        agent_core: AgentCore | None = None,
        page_scope_adapter: PageBuildScopeAdapter | None = None,
    ) -> None:
        super().__init__(chat_service=chat_service)
        self._agent_core = agent_core or AgentCore()
        self._page_scope_adapter = page_scope_adapter or PageBuildScopeAdapter()

    def compose_page_build_goal(
        self,
        *,
        prompt: str,
        recent_contexts: list[dict[str, Any]] | None = None,
        conversation_context: str = "",
    ) -> str:
        return self._agent_core.compose_build_goal(
            adapter=self._page_scope_adapter,
            request=BuildGoalRequest(
                user_prompt=prompt,
                recent_contexts=normalize_build_attempts(recent_contexts),
                conversation_context=conversation_context,
            ),
        )

    def apply_page_goal(
        self,
        *,
        page: models.Page,
        goal: str,
        workspace_store: WorkspaceStore | None = None,
        existing_functions: list[dict[str, Any]] | None = None,
    ) -> CodingEngineApplyResult:
        store = workspace_store or WorkspaceStore()
        return self._page_scope_adapter.apply_goal(
            workspace_store=store,
            target=page,
            goal=goal,
            existing_functions=existing_functions,
        )
