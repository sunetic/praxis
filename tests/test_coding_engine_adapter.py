from pathlib import Path

import pytest

from app.services.platform.coding_engine import AiderLikeAdapter, CodingEngineEdit, PiLiteAdapter


def test_aider_like_adapter_applies_edit_inside_workspace(tmp_path: Path):
    adapter = AiderLikeAdapter()
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    plan = adapter.plan_changes(
        goal="update function file",
        allowed_files=["main.py"],
        edits=[CodingEngineEdit(relative_path="main.py", content="print('ok')\n")],
    )
    result = adapter.apply_changes(workspace_dir=workspace, plan=plan)
    assert result.changed_files == ["main.py"]
    assert (workspace / "main.py").read_text(encoding="utf-8") == "print('ok')\n"


def test_aider_like_adapter_blocks_file_outside_allowed_scope(tmp_path: Path):
    adapter = AiderLikeAdapter()
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    plan = adapter.plan_changes(
        goal="malicious write",
        allowed_files=["main.py"],
        edits=[CodingEngineEdit(relative_path="../outside.py", content="print('bad')\n")],
    )
    with pytest.raises(ValueError):
        adapter.apply_changes(workspace_dir=workspace, plan=plan)


def test_pi_lite_adapter_applies_prepared_edits_without_llm(tmp_path: Path):
    adapter = PiLiteAdapter()
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    plan = adapter.plan_changes(
        goal="update function file",
        allowed_files=["main.py"],
        edits=[CodingEngineEdit(relative_path="main.py", content="print('pi-lite')\n")],
    )
    result = adapter.apply_changes(workspace_dir=workspace, plan=plan)
    assert result.changed_files == ["main.py"]
    assert (workspace / "main.py").read_text(encoding="utf-8") == "print('pi-lite')\n"
