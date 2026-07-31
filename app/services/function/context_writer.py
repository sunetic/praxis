from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.logging import fmt_kv, get_logger
from app.services.function.runtime_contract import get_function_runtime_contract
from app.services.function.scope_adapter import FunctionBuildScopeAdapter

logger = get_logger("services.function.context_writer")


class FunctionContextWriter:
    """
    Generates context files in the workspace for external coding engines.

    Writes CLAUDE.md (runtime contract, db schema, guardrails, workflow) to
    the workspace root so that Claude Code (and compatible tools) auto-discover
    it.  Datasource schema is passed at write-time so it can reflect the
    specific function being built.
    """

    def write(
        self,
        *,
        workspace_dir: Path,
        goal: str,
        datasource_schema: dict[str, Any] | None = None,
        datasource_id: int | None = None,
    ) -> None:
        claude_md = workspace_dir / "CLAUDE.md"
        content = self._build_content(
            goal=goal,
            datasource_schema=datasource_schema,
            datasource_id=datasource_id,
        )
        claude_md.write_text(content, encoding="utf-8")
        logger.info(
            "context_writer_done %s",
            fmt_kv(workspace=str(workspace_dir), has_schema=datasource_schema is not None),
        )

    def _build_content(
        self,
        *,
        goal: str,
        datasource_schema: dict[str, Any] | None,
        datasource_id: int | None,
    ) -> str:
        sections: list[str] = []

        sections.append("# Function Build Context\n")
        sections.append(
            "You are building a Praxis Function.\nFollow the rules and contract below strictly.\n"
        )

        # ── Runtime Contract ──
        contract = get_function_runtime_contract()
        sections.append("## Runtime Contract\n")
        sections.append(f"Contract version: `{contract.get('contract_version', 'unknown')}`\n")

        entrypoints = contract.get("entrypoints", {})
        main_ep = entrypoints.get("main", {})
        sections.append(
            "### Entry Point\n"
            "```python\n"
            f"{main_ep.get('signature', 'main(payload, context)')}\n"
            "```\n"
            "- `payload`: dict — user input\n"
            "- `context`: dict — runtime context (use `.get()` only, never attribute access)\n"
        )

        # DB API
        db_api = contract.get("db_api", {})
        if db_api:
            sections.append("### Database API\n")
            methods = db_api.get("methods", {})
            if isinstance(methods, dict):
                for name, spec in methods.items():
                    sig = spec.get("signature", name)
                    desc = spec.get("description", "")
                    sections.append(f"- `{sig}` — {desc}")
            elif isinstance(methods, list):
                for item in methods:
                    sections.append(f"- `{item}`")
            sections.append("")

        result_shape = db_api.get("result_shape", {})
        if result_shape:
            sections.append(
                "### DB Result Shape\n"
                "```python\n"
                "result = db.query_by_id(sql, datasource_id=ds_id)\n"
                "rows = result.get('rows', [])\n"
                "for row in rows:\n"
                "    print(row)\n"
                "```\n"
                "Never iterate `db.query()` return directly — always access `.get('rows', [])`.\n"
            )

        # Platform API
        platform_api = contract.get("platform_api", {})
        if platform_api:
            sections.append("### Platform API\n")
            methods = platform_api.get("methods", {})
            if isinstance(methods, dict):
                for name, spec in methods.items():
                    sig = spec.get("signature", name)
                    desc = spec.get("description", "")
                    sections.append(f"- `{sig}` — {desc}")
            elif isinstance(methods, list):
                for item in methods:
                    sections.append(f"- `{item}`")
            objects = platform_api.get("objects", [])
            if objects:
                sections.append(f"\nAvailable objects: {', '.join(objects)}")
            sections.append("")

        # ── Datasource Schema ──
        if datasource_schema:
            sections.append("## Available Database Schema\n")
            if datasource_id is not None:
                sections.append(f"Default datasource_id: `{datasource_id}`\n")
            tables = datasource_schema.get("tables", {})
            for table_name, columns in tables.items():
                col_list = (
                    ", ".join(str(c) for c in columns)
                    if isinstance(columns, list)
                    else str(columns)
                )
                sections.append(f"- **{table_name}**: {col_list}")
            sections.append("")

        # ── Guardrails ──
        adapter = FunctionBuildScopeAdapter()
        sections.append("## Guardrails\n")
        sections.append(adapter.guardrails())
        sections.append("")

        return "\n".join(sections)
