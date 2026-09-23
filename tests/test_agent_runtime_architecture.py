"""Structural guards, not evidence of conversational quality."""

import ast
from pathlib import Path

from app.services.agent.definitions import CHAT_INSTRUCTIONS, AgentDefinition

ROOT = Path(__file__).resolve().parents[1]
RETIRED_MODULES = {
    "app.tools.registry",
    "app.services.llm",
    "app.services.chat",
    "app.services.response_style",
    "app.services.agent.core",
    "app.services.agent.reasoning_engine",
    "app.services.agent.task_runtime",
    "app.services.agent.task_contract",
    "app.services.agent.task_contract_agent",
    "app.services.agent.build_verification_pipeline",
    "app.services.platform.coding_engine",
    "app.services.external_cli_adapter",
    "app.services.function.build_orchestrator",
    "app.services.function.chat_agent",
    "app.services.page.build_orchestrator",
    "app.services.page.chat_agent",
}


def test_retired_runtime_is_deleted_and_not_imported_by_product():
    for module in RETIRED_MODULES:
        path = ROOT.joinpath(*module.split("."))
        assert not path.with_suffix(".py").exists(), module
        assert not (path / "__init__.py").exists(), module
    for path in (ROOT / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            imports = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert not any(
                name == old or name.startswith(old + ".")
                for name in imports
                for old in RETIRED_MODULES
            ), path


def test_default_definition_uses_one_instruction_template_and_explicit_empty_tools():
    definition = AgentDefinition(name="no tools", tool_names=frozenset())
    assert definition.instructions == CHAT_INSTRUCTIONS
    assert definition.tool_names == frozenset()
