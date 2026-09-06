from pathlib import Path

from app.models import models
from app.services.platform.coding_engine import CodingEngineApplyResult, CodingEnginePlan
from app.services.platform.workspace_store import WorkspaceStore


class _StubGoalAdapter:
    def plan_changes(self, *, goal: str, allowed_files: list[str], edits: list):
        _ = goal
        _ = edits
        return CodingEnginePlan(goal="stub", allowed_files=allowed_files, edits=[])

    def apply_changes(
        self, *, workspace_dir: Path, plan: CodingEnginePlan
    ) -> CodingEngineApplyResult:
        if "main.py" in plan.allowed_files:
            (workspace_dir / "main.py").write_text(
                "def run(payload, context):\n    return {'ok': True}\n", encoding="utf-8"
            )
            changed = ["main.py"]
        elif "main.tsx" in plan.allowed_files:
            (workspace_dir / "main.tsx").write_text(
                "export default function Page(){return <div>ok</div>}\n", encoding="utf-8"
            )
            changed = ["main.tsx"]
            if "preview.html" in plan.allowed_files:
                (workspace_dir / "preview.html").write_text(
                    "<!doctype html><html><body><main><div>ok</div></main></body></html>\n",
                    encoding="utf-8",
                )
                changed.append("preview.html")
        else:
            changed = []
        return CodingEngineApplyResult(
            changed_files=changed,
            diff_summary="stub applied",
            tests_suggested=[],
            risk_notes=[],
            assistant_message="stub done",
        )


class _MirrorEditsAdapter:
    def plan_changes(self, *, goal: str, allowed_files: list[str], edits: list):
        _ = goal, allowed_files
        return CodingEnginePlan(goal="mirror", allowed_files=allowed_files, edits=edits)

    def apply_changes(
        self, *, workspace_dir: Path, plan: CodingEnginePlan
    ) -> CodingEngineApplyResult:
        changed: list[str] = []
        for edit in plan.edits:
            target = workspace_dir / edit.relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edit.content, encoding="utf-8")
            changed.append(edit.relative_path)
        return CodingEngineApplyResult(
            changed_files=changed,
            diff_summary="mirror applied",
            tests_suggested=[],
            risk_notes=[],
            assistant_message="mirror done",
        )


def test_workspace_store_apply_function_goal_updates_draft_code(tmp_path: Path):
    function = models.Function(
        name="f1", status="draft", draft_code="def run(payload, context):\n    return {}\n"
    )
    function.id = 101
    store = WorkspaceStore(root=tmp_path / "ws", adapter=_StubGoalAdapter())

    result = store.apply_function_goal(function=function, goal="make it useful")

    assert result.changed_files == ["main.py"]
    assert "return {'ok': True}" in str(function.draft_code or "")
