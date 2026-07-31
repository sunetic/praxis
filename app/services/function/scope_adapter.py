from __future__ import annotations

from typing import Any

from app.models import models
from app.services.agent.core import BuildAttemptContext
from app.services.agent.scope_adapter_base import BuildApplyAdapter, _ContinuationIntentAdapter
from app.services.function.runtime_contract import get_function_runtime_contract_block
from app.services.llm import LLMClient
from app.services.platform.coding_engine import CodingEngineApplyResult
from app.services.platform.workspace_store import WorkspaceStore


class FunctionBuildScopeAdapter(_ContinuationIntentAdapter, BuildApplyAdapter):
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        super().__init__(llm_client=llm_client)

    def resolve_primary_requirement(
        self, *, prompt: str, history: list[BuildAttemptContext]
    ) -> str:
        return self._resolve_primary_requirement_with_llm(prompt=prompt, history=history)

    def guardrails(self) -> str:
        return (
            "Implementation Guardrails:\n"
            "1) Entry: implement `main(payload, context)` or class-based `FunctionBase.run(self, payload, context)`.\n"
            "2) Context: `context` is a plain dict for declared scalar fields only — use `context.get('datasource_id')` and similar scalar keys; "
            "never use `context.db`, `context.platform`, `context.session`, or attribute-style access.\n"
            "3) Capabilities: `db`, `platform`, and `scheduler_history` are injected globals. Use them directly instead of reading them from `context`.\n"
            "4) Data access: use `platform.list/get` for object/datasource metadata; use `db.query_by_id(sql, datasource_id=...)` for user-tenant SQL. "
            "When context.get('datasource_id') is None, fall back via `platform.list('datasource')` to get the first available id.\n"
            "5) Contract fidelity: only use declared methods and canonical field names from the runtime contract. Do not invent undeclared params. "
            "Call `get_function_runtime_contract` if unsure. For scheduler history use `scheduler_history.list/delete` helpers, not datasource SQL.\n"
            f"6) {get_function_runtime_contract_block()}\n"
            "7) Safety: no hardcoded credentials; no mock/fake return data; Do not return mock/fake/placeholder datasource data; "
            "re-raise DB/platform exceptions after adding context — "
            "NEVER wrap db.query_by_id or platform calls in bare try/except that swallows exceptions silently. "
            "If you catch an exception, you MUST re-raise it (e.g. `except Exception as e: raise RuntimeError(...) from e`). "
            "Returning empty results or default values on exception is forbidden.\n"
            "8) assistant_message: business-oriented, user-facing language only — no platform internals (get_session_by_id / SQLAlchemy / execution_mode / plan mode / etc.).\n"
        )

    def apply_goal(
        self,
        *,
        workspace_store: WorkspaceStore,
        target: Any,
        goal: str,
        datasource_schema: dict[str, Any] | None = None,
        datasource_id: int | None = None,
    ) -> CodingEngineApplyResult:
        if not isinstance(target, models.Function):
            raise TypeError("FunctionBuildScopeAdapter requires models.Function target")
        return workspace_store.apply_function_goal(
            function=target,
            goal=goal,
            datasource_schema=datasource_schema,
            datasource_id=datasource_id,
        )
