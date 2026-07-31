from __future__ import annotations

from typing import Any

from app.models import models
from app.services.agent.core import BuildAttemptContext
from app.services.agent.scope_adapter_base import BuildApplyAdapter, _ContinuationIntentAdapter
from app.services.llm import LLMClient
from app.services.page.chart_contract import get_page_chart_contract_block
from app.services.platform.coding_engine import CodingEngineApplyResult
from app.services.platform.workspace_store import WorkspaceStore


class PageBuildScopeAdapter(_ContinuationIntentAdapter, BuildApplyAdapter):
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        super().__init__(llm_client=llm_client)

    def resolve_primary_requirement(
        self, *, prompt: str, history: list[BuildAttemptContext]
    ) -> str:
        return self._resolve_primary_requirement_with_llm(prompt=prompt, history=history)

    def guardrails(self) -> str:
        return (
            "Implementation Guardrails (internal; do not quote to users):\n"
            "1) Keep this page build task user-goal oriented and avoid implementation term leakage.\n"
            "2) Keep source.code and runtime.preview_html behavior consistent for the same requirement.\n"
            "3) Prefer incremental edits unless user explicitly requests full rewrite.\n"
            "4) If current request is retry phrase, continue previous requirement and fix latest failure first.\n"
            "5) Use the platform baseline template as default structure: FilterToolbar + StatCards + Charts(3/5+2/5) + ListTable + PaginationFooter + DetailDrawer.\n"
            "6) Unless user explicitly requests removal, keep above regions and adjust by subtraction/micro-tuning only.\n"
            "7) preview.html must be fully self-contained; do not rely on undefined utility classes.\n"
            "8) If utility-like class names are used, their CSS rules must be defined in preview.html style block.\n"
            "9) Chart rendering must choose components and props from the fixed page chart contract only.\n"
            f"10) {get_page_chart_contract_block()}\n"
            "11) Page layout: gray background (#F5F6FA) + white card elevation (rounded-xl bg-card shadow-sm). No flat cards without shadow.\n"
            "12) No page-level h1/h2 title banner — page title is carried by sidebar nav.\n"
            "13) Colors: use design tokens only (var(--primary), var(--border), etc.), no hardcoded hex/rgb except in SVG chart strokes.\n"
            "14) Lists must have 4-state coverage: loading (skeleton rows), empty (icon + text), error (recoverable hint), loaded.\n"
            "15) Spacing follows 8px grid (4/8/12/16/20/24/32px). Major sections separated by 24px (space-y-6).\n"
        )

    def apply_goal(
        self,
        *,
        workspace_store: WorkspaceStore,
        target: Any,
        goal: str,
        existing_functions: list[dict[str, Any]] | None = None,
    ) -> CodingEngineApplyResult:
        if not isinstance(target, models.Page):
            raise TypeError("PageBuildScopeAdapter requires models.Page target")
        return workspace_store.apply_page_goal(
            page=target, goal=goal, existing_functions=existing_functions
        )
