from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.logging import fmt_kv, get_logger
from app.services.page.chart_contract import get_page_chart_contract
from app.services.page.scope_adapter import PageBuildScopeAdapter

logger = get_logger("services.page.context_writer")


class PageContextWriter:
    """
    Generates context files in the workspace for external coding engines
    building Pages.

    Writes CLAUDE.md (runtime contract, template catalog, guardrails,
    5-stage workflow) to the workspace root so that Claude Code (and
    compatible tools) auto-discover it.
    """

    def write(
        self,
        *,
        workspace_dir: Path,
        goal: str,
        existing_functions: list[dict[str, Any]] | None = None,
    ) -> None:
        claude_md = workspace_dir / "CLAUDE.md"
        content = self._build_content(
            goal=goal,
            existing_functions=existing_functions,
        )
        claude_md.write_text(content, encoding="utf-8")
        logger.info(
            "page_context_writer_done %s",
            fmt_kv(workspace=str(workspace_dir)),
        )

    def _build_content(
        self,
        *,
        goal: str,
        existing_functions: list[dict[str, Any]] | None,
    ) -> str:
        sections: list[str] = []

        sections.append("# Page Build Context\n")
        sections.append(
            "You are building a Praxis Page.\n"
            "Follow the rules and contract below strictly.\n"
        )

        # ── Template Catalog ──
        sections.append("## Template Catalog\n")
        sections.append(
            "Choose one of the following templates as your structural baseline:\n"
        )
        sections.append(
            "- **data_workbench**: FilterToolbar + StatCards + Charts(3/5+2/5) + ListTable + PaginationFooter + DetailDrawer. "
            "Best for diagnostics/inspection/reporting pages.\n"
            "- **diagnostic_flow**: ScopeSelector + TimeRangePicker + ResultPanel + ListTable + PaginationFooter + DetailDrawer. "
            "Best for problem-list scenarios.\n"
            "- **config_form**: FormSections + PrimaryAction + RecentRunsTable. "
            "Best for input-then-query scenarios.\n"
        )

        # ── Chart Contract ──
        chart_contract = get_page_chart_contract()
        sections.append("## Chart Contract\n")
        sections.append(
            f"Contract version: `{chart_contract.get('contract_version', 'unknown')}`\n"
        )
        components = chart_contract.get("components", {})
        for comp_id, spec in components.items():
            desc = spec.get("description", "")
            sections.append(f"- **{comp_id}**: {desc}")
        sections.append("")

        # ── Available Functions ──
        if existing_functions:
            sections.append("## Available Functions\n")
            sections.append(
                "These Functions already exist on the platform. "
                "Your Page can call them via the runtime API. "
                "Prefer reusing existing Functions over duplicating logic.\n"
            )
            for fn in existing_functions:
                name = fn.get("name", "unnamed")
                desc = fn.get("description", "")
                fn_id = fn.get("id", "")
                sections.append(f"- **{name}** (id={fn_id}): {desc}")
            sections.append("")

        # ── Guardrails ──
        adapter = PageBuildScopeAdapter()
        sections.append("## Guardrails\n")
        sections.append(adapter.guardrails())
        sections.append("")

        # ── Workflow ──
        sections.append(self._workflow_section())

        return "\n".join(sections)

    @staticmethod
    def _workflow_section() -> str:
        return (
            "## Development Workflow\n\n"
            "You MUST follow these stages in order. Do NOT skip to coding before completing stages 1-3.\n\n"
            "### Stage 1: Complexity Assessment\n\n"
            "Evaluate whether the goal can be fulfilled by a single Page.\n\n"
            "- If the goal requires multiple unrelated views that don't share a common scope, it is TOO COMPLEX.\n"
            "- If the goal requires Functions that don't exist yet and can't be created inline, it NEEDS CLARIFICATION.\n"
            "- If the goal is clear and maps to one of the template structures, PROCEED.\n\n"
            "When the goal is too complex or needs clarification:\n"
            "- Do NOT write any code.\n"
            "- Return a JSON result: `{\"result_status\": \"too_complex\", \"result\": \"<explanation and suggested decomposition>\"}` "
            "or `{\"result_status\": \"needs_clarification\", \"result\": \"<specific questions>\"}`.\n\n"
            "### Stage 2: Requirement Refinement\n\n"
            "If the goal passes complexity assessment:\n"
            "- Identify which template to use as structural baseline.\n"
            "- Determine which Functions the Page needs to call (existing or new).\n"
            "- Plan the data flow: which Function provides data for which UI region.\n"
            "- Note any edge cases (empty data, loading states, error states).\n\n"
            "### Stage 3: Implementation Plan\n\n"
            "Before writing code, plan:\n"
            "- Function dependency map: which Functions to call and when.\n"
            "- Page region mapping: which template regions display which data.\n"
            "- Chart selection: which chart types from the contract to use.\n"
            "- Interaction flow: filters, drill-down, drawer triggers.\n\n"
            "### Stage 4: Implementation\n\n"
            "Generate the Page source code following the selected template structure.\n\n"
            "Rules:\n"
            "- preview.html must be fully self-contained; no external dependencies.\n"
            "- Use design tokens only (var(--primary), var(--border), etc.).\n"
            "- Follow 8px grid spacing (4/8/12/16/20/24/32px).\n"
            "- Gray background (#F5F6FA) + white card elevation (rounded-xl bg-card shadow-sm).\n"
            "- Lists must have 4-state coverage: loading, empty, error, loaded.\n"
            "- Charts must use components/props from the chart contract only.\n\n"
            "### Stage 5: Self-Test\n\n"
            "You MUST verify before finishing:\n"
            "- preview.html renders without errors in a browser.\n"
            "- All template regions are present and populated.\n"
            "- Chart components match the chart contract.\n"
            "- No undefined CSS utility classes.\n"
            "- The Page actually answers the user's goal.\n"
        )
