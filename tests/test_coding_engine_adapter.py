from pathlib import Path

from app.services.platform.coding_engine import (
    BuiltinReasoningAdapter,
    CodingEngineEdit,
)


def test_builtin_reasoning_adapter_applies_prepared_edits_without_llm(tmp_path: Path):
    adapter = BuiltinReasoningAdapter()
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    plan = adapter.plan_changes(
        goal="update function file",
        allowed_files=["main.py"],
        edits=[CodingEngineEdit(relative_path="main.py", content="print('reasoning')\n")],
    )
    result = adapter.apply_changes(workspace_dir=workspace, plan=plan)
    assert result.changed_files == ["main.py"]
    assert (workspace / "main.py").read_text(encoding="utf-8") == "print('reasoning')\n"
