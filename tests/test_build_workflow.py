"""Tests for the 5-stage build workflow: complexity assessment, requirement
refinement, implementation plan, implementation, self-test.

Covers: CodingEngineApplyResult.result_status, _build_prompt workflow
content, and ExternalCliAdapter result_status parsing.
"""

import subprocess
from pathlib import Path

import pytest

from app.services.platform.coding_engine import CodingEngineApplyResult

# ── CodingEngineApplyResult.result_status ──


def test_result_status_defaults_to_completed():
    result = CodingEngineApplyResult(
        changed_files=["main.py"],
        diff_summary="1 file",
        tests_suggested=[],
        risk_notes=[],
        assistant_message="done",
    )
    assert result.result_status == "completed"


def test_result_status_accepts_too_complex():
    result = CodingEngineApplyResult(
        changed_files=[],
        diff_summary="no changes",
        tests_suggested=[],
        risk_notes=[],
        assistant_message="This goal is too complex",
        result_status="too_complex",
    )
    assert result.result_status == "too_complex"
    assert result.changed_files == []


def test_result_status_accepts_needs_clarification():
    result = CodingEngineApplyResult(
        changed_files=[],
        diff_summary="no changes",
        tests_suggested=[],
        risk_notes=[],
        assistant_message="Which datasource?",
        result_status="needs_clarification",
    )
    assert result.result_status == "needs_clarification"


def test_result_status_backward_compatible_with_existing_fields():
    """Old callers that don't pass result_status still work."""
    result = CodingEngineApplyResult(
        changed_files=["main.py"],
        diff_summary="1 file",
        tests_suggested=["test_main"],
        risk_notes=["minor"],
        assistant_message="ok",
        generated_title="Title",
        generated_description="Desc",
    )
    assert result.result_status == "completed"
    assert result.generated_title == "Title"


# ── _build_prompt workflow instructions ──


def test_build_prompt_includes_five_stage_workflow():
    from app.services.external_cli_adapter import _build_prompt

    prompt = _build_prompt("Build a CPU monitor function", ["main.py"])
    assert "COMPLEXITY ASSESSMENT" in prompt
    assert "REQUIREMENT REFINEMENT" in prompt
    assert "IMPLEMENTATION PLAN" in prompt
    assert "IMPLEMENTATION" in prompt
    assert "SELF-TEST" in prompt


def test_build_prompt_includes_result_status_protocol():
    from app.services.external_cli_adapter import _build_prompt

    prompt = _build_prompt("Build something", [])
    assert "result_status=too_complex" in prompt
    assert "result_status=needs_clarification" in prompt


def test_build_prompt_preserves_goal_and_allowed_files():
    from app.services.external_cli_adapter import _build_prompt

    prompt = _build_prompt("Query active sessions", ["main.py", "helper.py"])
    assert "Goal: Query active sessions" in prompt
    assert "Only modify these files: main.py, helper.py" in prompt


def test_build_prompt_omits_allowed_files_when_empty():
    from app.services.external_cli_adapter import _build_prompt

    prompt = _build_prompt("Test goal", [])
    assert "Only modify" not in prompt


# ── ExternalCliAdapter result_status parsing ──


def test_parse_cli_json_extracts_result_status():
    import json

    from app.services.external_cli_adapter import _parse_cli_json

    raw = json.dumps({"result_status": "too_complex", "result": "Split this"})
    parsed = _parse_cli_json(raw)
    assert parsed is not None
    assert parsed["result_status"] == "too_complex"


def test_parse_cli_json_handles_missing_result_status():
    import json

    from app.services.external_cli_adapter import _parse_cli_json

    raw = json.dumps({"result": "Function built successfully"})
    parsed = _parse_cli_json(raw)
    assert parsed is not None
    assert "result_status" not in parsed


def test_parse_cli_json_returns_none_for_empty():
    from app.services.external_cli_adapter import _parse_cli_json

    assert _parse_cli_json("") is None
    assert _parse_cli_json("   ") is None


def test_parse_cli_json_extracts_last_object_after_progress_and_ansi():
    from app.services.external_cli_adapter import _parse_cli_json

    raw = (
        "Running tests...\n"
        '{"type":"progress","message":"halfway"}\n'
        '{"result":"done","result_status":"completed"}\x1b[?25h'
    )
    parsed = _parse_cli_json(raw)
    assert parsed is not None
    assert parsed["result"] == "done"
    assert parsed["result_status"] == "completed"


def test_summarize_engine_output_line_filters_prompt_echo():
    from app.services.external_cli_adapter import _summarize_engine_output_line

    assert (
        _summarize_engine_output_line("Follow this workflow strictly: (1) COMPLEXITY ASSESSMENT")
        is None
    )


def test_summarize_engine_output_line_extracts_structured_message():
    from app.services.external_cli_adapter import _summarize_engine_output_line

    summary = _summarize_engine_output_line('{"type":"progress","message":"Running tests"}')
    assert summary == "Running tests"


def test_summarize_engine_output_line_normalizes_command_output():
    from app.services.external_cli_adapter import _summarize_engine_output_line

    summary = _summarize_engine_output_line("⠋ Running pytest tests/test_build_workflow.py")
    assert summary == "执行命令: pytest tests/test_build_workflow.py"


def test_external_cli_timeout_reports_last_engine_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from app.services.external_cli_adapter import ExternalCliAdapter

    adapter = ExternalCliAdapter(command="cfuse", context_writer=None, timeout_seconds=30)
    plan = adapter.plan_changes(goal="Build function", allowed_files=["main.py"], edits=[])

    def _raise_timeout(command: str, *, cwd: str) -> tuple[str, str, int]:
        del command, cwd
        adapter._last_stream_summary = "执行命令: pytest tests/test_build_workflow.py"
        raise subprocess.TimeoutExpired(cmd="cfuse", timeout=30)

    monkeypatch.setattr(adapter, "_run_streaming", _raise_timeout)

    result = adapter.apply_changes(workspace_dir=tmp_path, plan=plan)

    assert result.diff_summary == "External CLI timed out"
    assert "Last observed engine output" in result.assistant_message
    assert any("pytest tests/test_build_workflow.py" in note for note in result.risk_notes)


# ── FunctionContextWriter 5-stage workflow ──


def test_function_context_writer_no_workflow_section():
    """Workflow instructions are now orchestrator-driven; CLAUDE.md provides context only."""
    from app.services.function.context_writer import FunctionContextWriter

    assert not hasattr(FunctionContextWriter, "_workflow_section"), (
        "_workflow_section should have been removed; workflow is now in StagedFunctionBuildOrchestrator"
    )


def test_function_context_writer_claude_md_has_no_workflow_instructions(tmp_path: Path):
    """CLAUDE.md must NOT contain workflow stage instructions — those are orchestrator-controlled."""
    from app.services.function.context_writer import FunctionContextWriter

    writer = FunctionContextWriter()
    writer.write(workspace_dir=tmp_path, goal="test goal")
    content = (tmp_path / "CLAUDE.md").read_text()
    assert "## Development Workflow" not in content, "Workflow section must not be in CLAUDE.md"
    assert "Stage 1: Complexity Assessment" not in content
    assert "Stage 4: Implementation" not in content


def test_function_context_writer_claude_md_has_runtime_contract_and_guardrails(tmp_path: Path):
    """CLAUDE.md must retain runtime contract and guardrails for the coding engine."""
    from app.services.function.context_writer import FunctionContextWriter

    writer = FunctionContextWriter()
    writer.write(workspace_dir=tmp_path, goal="test goal")
    content = (tmp_path / "CLAUDE.md").read_text()
    assert "## Runtime Contract" in content
    assert "## Guardrails" in content
    assert "db = context.get('db')" not in content
    assert "injected" in content


def test_function_context_writer_writes_to_workspace(tmp_path: Path):
    from app.services.function.context_writer import FunctionContextWriter

    writer = FunctionContextWriter()
    writer.write(
        workspace_dir=tmp_path,
        goal="Test goal",
        datasource_schema={"tables": {"users": ["id", "name"]}},
        datasource_id=42,
    )
    claude_md = tmp_path / "CLAUDE.md"
    assert claude_md.exists()
    content = claude_md.read_text(encoding="utf-8")
    assert "Function Build Context" in content
    assert "## Runtime Contract" in content
    assert "## Guardrails" in content
    assert "users" in content
    assert "42" in content
    # Workflow instructions are now orchestrator-driven, not in CLAUDE.md
    assert "Stage 1: Complexity Assessment" not in content


# ── PageContextWriter ──
