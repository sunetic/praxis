from pathlib import Path

import pytest

from app.services.platform.coding_engine import (
    BuiltinReasoningAdapter,
    CodingEngineApplyResult,
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


def test_builtin_reasoning_adapter_routes_function_plan_to_chat_agent_and_preserves_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    captured: dict[str, object] = {}

    async def fake_run_coding_task(self, **kwargs):
        del self
        captured.update(kwargs)
        return CodingEngineApplyResult(
            changed_files=[],
            diff_summary="",
            tests_suggested=[],
            risk_notes=[],
            assistant_message="Split this request.",
            result_status="too_complex",
        )

    monkeypatch.setattr(
        "app.services.function.chat_agent.FunctionChatAgent.run_coding_task",
        fake_run_coding_task,
    )
    adapter = BuiltinReasoningAdapter()
    adapter.set_build_context(
        datasource_schema={"tables": {"users": ["id"]}},
        datasource_id=7,
    )
    plan = adapter.plan_changes(
        goal="Assess request",
        allowed_files=["main.py"],
        edits=[],
    )
    plan = type(plan)(
        goal=plan.goal,
        allowed_files=plan.allowed_files,
        edits=plan.edits,
        purpose="analyze",
    )

    result = adapter.apply_changes(workspace_dir=tmp_path, plan=plan)

    assert result.result_status == "too_complex"
    assert captured["purpose"] == "analyze"
    assert captured["build_context"] == {
        "datasource_schema": {"tables": {"users": ["id"]}},
        "datasource_id": 7,
    }


def test_builtin_reasoning_adapter_routes_page_plan_to_chat_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    captured: dict[str, object] = {}

    async def fake_run_coding_task(self, **kwargs):
        del self
        captured.update(kwargs)
        return CodingEngineApplyResult(
            changed_files=["main.tsx", "preview.html"],
            diff_summary="Page updated",
            tests_suggested=[],
            risk_notes=[],
            assistant_message="Page updated.",
        )

    monkeypatch.setattr(
        "app.services.page.chat_agent.PageChatAgent.run_coding_task",
        fake_run_coding_task,
    )
    adapter = BuiltinReasoningAdapter(max_iterations=12)
    plan = adapter.plan_changes(
        goal="Build a capacity dashboard",
        allowed_files=["main.tsx", "preview.html"],
        edits=[],
    )
    plan = type(plan)(
        goal=plan.goal,
        allowed_files=plan.allowed_files,
        edits=plan.edits,
        context={"existing_functions": [{"id": 1, "name": "Table capacity"}]},
    )

    result = adapter.apply_changes(workspace_dir=tmp_path, plan=plan)

    assert result.changed_files == ["main.tsx", "preview.html"]
    assert captured["goal"] == "Build a capacity dashboard"
    assert captured["build_context"] == {
        "existing_functions": [{"id": 1, "name": "Table capacity"}]
    }
    assert captured["max_iterations"] == 12
