from __future__ import annotations

import json
import os
import shlex
import subprocess
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.logging import fmt_kv, get_logger
from app.models import models
from app.services.platform.coding_engine import (
    AiderLikeAdapter,
    CodingEngineAdapter,
    CodingEngineApplyResult,
    CodingEngineEdit,
    CodingEnginePlan,
    PiLiteAdapter,
)

try:
    from app.services.page.preview_theme import (
        build_default_page_preview_html as _build_default_page_preview_html,
    )
    from app.services.page.preview_theme import (
        build_default_page_source_code as _build_default_page_source_code,
    )
    from app.services.page.preview_theme import (
        ensure_page_preview_theme as _ensure_page_preview_theme,
    )

    _has_page = True
except ImportError:
    _has_page = False

logger = get_logger("app.services.workspace_store")
_LEGACY_PAGE_TEMPLATE_HINTS: tuple[str, ...] = (
    "You can describe requirements in the Build Chat panel on the right; the page will be modified based on this template.",
    "After describing page requirements, the results will appear here.",
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _split_shell_words(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _strip_duplicate_flags(command: str, pre_flags: str, post_flags: str) -> str:
    command_words = _split_shell_words(command)
    if not command_words:
        return ""
    pre_words = _split_shell_words(pre_flags)
    post_words = _split_shell_words(post_flags)
    if pre_words and command_words[-len(pre_words) :] == pre_words:
        command_words = command_words[: -len(pre_words)]
    if post_words and command_words[-len(post_words) :] == post_words:
        command_words = command_words[: -len(post_words)]
    if post_words and len(command_words) >= len(post_words):
        max_start = len(command_words) - len(post_words)
        for start in range(max(1, max_start), max_start + 1):
            if command_words[start : start + len(post_words)] == post_words:
                del command_words[start : start + len(post_words)]
                break
    if pre_words and len(command_words) >= len(pre_words):
        max_start = len(command_words) - len(pre_words)
        for start in range(1, max_start + 1):
            if command_words[start : start + len(pre_words)] == pre_words:
                del command_words[start : start + len(pre_words)]
                break
    return " ".join(command_words)


def _resolve_adapter() -> CodingEngineAdapter:
    """Resolve the coding engine adapter from platform settings, falling back to env var."""
    engine_name = str(os.getenv("PRAXIS_CODING_ENGINE", "pi_lite")).strip().lower()
    external_cli_command = ""
    pre_flags = ""
    post_flags = ""
    try:
        from app.api.settings import get_setting
        from app.db.database import SessionLocal

        db = SessionLocal()
        try:
            stored = get_setting(db, "build_engine")
            if stored:
                engine_name = str(stored).strip().lower()
            stored_cmd = get_setting(db, "external_cli_command")
            if stored_cmd:
                external_cli_command = str(stored_cmd).strip()
            stored_pre = get_setting(db, "external_cli_pre_flags")
            if stored_pre:
                pre_flags = str(stored_pre).strip()
            stored_post = get_setting(db, "external_cli_post_flags")
            if stored_post:
                post_flags = str(stored_post).strip()
            if external_cli_command:
                external_cli_command = _strip_duplicate_flags(
                    external_cli_command, pre_flags, post_flags
                )
        finally:
            db.close()
    except Exception:
        pass  # settings table may not exist yet; fall back to env var

    if engine_name == "external_cli":
        if not external_cli_command:
            logger.warning("external_cli_engine_no_command fallback=pi_lite")
            return PiLiteAdapter()
        from app.services.external_cli_adapter import ExternalCliAdapter
        from app.services.function.context_writer import FunctionContextWriter

        return ExternalCliAdapter(
            command=external_cli_command,
            pre_flags=pre_flags,
            post_flags=post_flags,
            context_writer=FunctionContextWriter(),
        )
    if engine_name == "aider_like":
        return AiderLikeAdapter()
    if engine_name == "pi_lite":
        return PiLiteAdapter()
    raise ValueError(f"Unsupported coding engine: {engine_name}")


class WorkspaceStore:
    """
    File workspace and local git store.

    Code source of truth lives in workspace files + local git history.
    DB remains source of truth for lifecycle/governance metadata.
    """

    def __init__(
        self, root: Path | None = None, adapter: CodingEngineAdapter | None = None
    ) -> None:
        configured = os.getenv("PRAXIS_WORKSPACE_ROOT")
        if root is not None:
            self.root = root
        elif configured:
            self.root = Path(configured).expanduser()
        else:
            self.root = Path.home() / ".praxis" / "workspace"
        self.objects_root = self.root / "objects"
        self.objects_root.mkdir(parents=True, exist_ok=True)
        self._ensure_git_repo()
        if adapter is not None:
            self._adapter = adapter
            logger.info(
                "workspace_store_adapter %s",
                fmt_kv(adapter_type=type(adapter).__name__, root=self.root),
            )
        else:
            self._adapter = _resolve_adapter()
            logger.info(
                "workspace_store_adapter %s",
                fmt_kv(adapter_type=type(self._adapter).__name__, root=self.root),
            )

    def sync_function_draft(self, function: models.Function) -> Path:
        target_dir = self.objects_root / "functions" / str(function.id)
        target_dir.mkdir(parents=True, exist_ok=True)
        main_path = target_dir / "main.py"
        manifest_path = target_dir / "manifest.json"
        main_code = str(function.draft_code or "")
        manifest = self._load_manifest(manifest_path)
        manifest.update(
            {
                "object_type": "function",
                "object_id": function.id,
                "entry_file": "main.py",
                "allowed_files": ["main.py", "manifest.json"],
                "updated_at": _utc_now_iso(),
            }
        )
        plan = self._adapter.plan_changes(
            goal=f"sync function draft #{function.id}",
            allowed_files=["main.py", "manifest.json"],
            edits=[
                CodingEngineEdit(relative_path="main.py", content=main_code),
                CodingEngineEdit(
                    relative_path="manifest.json",
                    content=json.dumps(manifest, ensure_ascii=False, indent=2),
                ),
            ],
        )
        self._adapter.apply_changes(workspace_dir=target_dir, plan=plan)
        return main_path

    def sync_page_draft(self, page: models.Page) -> Path:
        if not _has_page:
            raise RuntimeError("Page capability not available (EE-only)")
        target_dir = self.objects_root / "pages" / str(page.id)
        target_dir.mkdir(parents=True, exist_ok=True)
        main_path = target_dir / "main.tsx"
        manifest_path = target_dir / "manifest.json"
        draft_payload = page.draft_payload if isinstance(page.draft_payload, dict) else {}
        source = (
            draft_payload.get("source") if isinstance(draft_payload.get("source"), dict) else {}
        )
        runtime = (
            draft_payload.get("runtime") if isinstance(draft_payload.get("runtime"), dict) else {}
        )
        source_code = str(source.get("code") or "")
        preview_html = str(runtime.get("preview_html") or "")
        if self._should_refresh_legacy_page_baseline(
            source_code=source_code, preview_html=preview_html
        ):
            source_code = _build_default_page_source_code()
            preview_html = _build_default_page_preview_html()
        else:
            if not source_code.strip():
                source_code = _build_default_page_source_code()
            if not preview_html.strip():
                preview_html = _build_default_page_preview_html()
        preview_html = _ensure_page_preview_theme(preview_html)
        manifest = self._load_manifest(manifest_path)
        manifest.update(
            {
                "object_type": "page",
                "object_id": page.id,
                "entry_file": "main.tsx",
                "allowed_files": ["main.tsx", "preview.html", "manifest.json"],
                "updated_at": _utc_now_iso(),
            }
        )
        plan = self._adapter.plan_changes(
            goal=f"sync page draft #{page.id}",
            allowed_files=["main.tsx", "preview.html", "manifest.json"],
            edits=[
                CodingEngineEdit(relative_path="main.tsx", content=source_code),
                CodingEngineEdit(relative_path="preview.html", content=preview_html),
                CodingEngineEdit(
                    relative_path="manifest.json",
                    content=json.dumps(manifest, ensure_ascii=False, indent=2),
                ),
            ],
        )
        self._adapter.apply_changes(workspace_dir=target_dir, plan=plan)
        return main_path

    def _should_refresh_legacy_page_baseline(self, *, source_code: str, preview_html: str) -> bool:
        merged = f"{str(source_code or '')}\n{str(preview_html or '')}"
        return any(marker in merged for marker in _LEGACY_PAGE_TEMPLATE_HINTS)

    def set_adapter_event_callback(self, cb: Any) -> None:
        """Forward event callback to adapter if it supports streaming."""
        if hasattr(self._adapter, "set_event_callback"):
            self._adapter.set_event_callback(cb)

    def analyze_function_goal(
        self,
        *,
        function: models.Function,
        stage_prompt: str,
        datasource_schema: dict[str, Any] | None = None,
        datasource_id: int | None = None,
    ) -> CodingEngineApplyResult:
        """Call the coding engine for analysis only — does NOT update function.draft_code.

        Used for Stage 1 (complexity assessment) and Stage 2 (requirement refinement)
        where the engine returns a JSON analysis result rather than writing code.
        """
        target_dir = self.objects_root / "functions" / str(function.id)
        target_dir.mkdir(parents=True, exist_ok=True)
        self.sync_function_draft(function)
        if hasattr(self._adapter, "set_build_context"):
            self._adapter.set_build_context(
                datasource_schema=datasource_schema,
                datasource_id=datasource_id,
            )
        plan: CodingEnginePlan = self._adapter.plan_changes(
            goal=str(stage_prompt or "").strip(),
            allowed_files=["main.py"],
            edits=[],
        )
        result = self._adapter.apply_changes(workspace_dir=target_dir, plan=plan)
        logger.info(
            "workspace_analyze_function_goal %s",
            fmt_kv(
                function_id=function.id,
                result_status=result.result_status,
                adapter=type(self._adapter).__name__,
            ),
        )
        return result

    def apply_function_goal(
        self,
        *,
        function: models.Function,
        goal: str,
        datasource_schema: dict[str, Any] | None = None,
        datasource_id: int | None = None,
    ) -> CodingEngineApplyResult:
        target_dir = self.objects_root / "functions" / str(function.id)
        target_dir.mkdir(parents=True, exist_ok=True)
        self.sync_function_draft(function)
        # Forward datasource info to the adapter for context injection.
        if hasattr(self._adapter, "set_build_context"):
            self._adapter.set_build_context(
                datasource_schema=datasource_schema,
                datasource_id=datasource_id,
            )
        plan: CodingEnginePlan = self._adapter.plan_changes(
            goal=str(goal or "").strip(),
            allowed_files=["main.py"],
            edits=[],
        )
        result = self._adapter.apply_changes(workspace_dir=target_dir, plan=plan)
        logger.info(
            "workspace_apply_function_goal %s",
            fmt_kv(
                function_id=function.id,
                changed_files=",".join(result.changed_files),
                adapter=type(self._adapter).__name__,
            ),
        )
        main_path = target_dir / "main.py"
        if main_path.exists():
            function.draft_code = main_path.read_text(encoding="utf-8")
        return result

    def apply_page_goal(
        self,
        *,
        page: models.Page,
        goal: str,
        existing_functions: list[dict[str, Any]] | None = None,
    ) -> CodingEngineApplyResult:
        if not _has_page:
            raise RuntimeError("Page capability not available (EE-only)")
        target_dir = self.objects_root / "pages" / str(page.id)
        target_dir.mkdir(parents=True, exist_ok=True)
        self.sync_page_draft(page)
        from app.services.page.context_writer import PageContextWriter

        PageContextWriter().write(
            workspace_dir=target_dir,
            goal=goal,
            existing_functions=existing_functions,
        )
        plan: CodingEnginePlan = self._adapter.plan_changes(
            goal=str(goal or "").strip(),
            allowed_files=["main.tsx", "preview.html"],
            edits=[],
        )
        result = self._adapter.apply_changes(workspace_dir=target_dir, plan=plan)
        logger.info(
            "workspace_apply_page_goal %s",
            fmt_kv(
                page_id=page.id,
                changed_files=",".join(result.changed_files),
                adapter=type(self._adapter).__name__,
            ),
        )
        main_path = target_dir / "main.tsx"
        preview_path = target_dir / "preview.html"
        if main_path.exists():
            code = main_path.read_text(encoding="utf-8")
            draft_payload = (
                deepcopy(page.draft_payload) if isinstance(page.draft_payload, dict) else {}
            )
            source = (
                deepcopy(draft_payload.get("source"))
                if isinstance(draft_payload.get("source"), dict)
                else {}
            )
            runtime = (
                deepcopy(draft_payload.get("runtime"))
                if isinstance(draft_payload.get("runtime"), dict)
                else {}
            )
            source["code"] = code
            source["language"] = str(source.get("language") or "tsx")
            draft_payload["source"] = source
            runtime["framework"] = str(runtime.get("framework") or "html")
            if preview_path.exists():
                runtime["preview_html"] = _ensure_page_preview_theme(
                    preview_path.read_text(encoding="utf-8")
                )
            else:
                runtime["preview_html"] = _ensure_page_preview_theme(
                    str(runtime.get("preview_html") or _build_default_page_preview_html())
                )
            draft_payload["runtime"] = runtime
            draft_payload["version"] = "page-runtime-v2"
            page.draft_payload = draft_payload
        return result

    def commit_publish(
        self, *, object_type: str, object_id: int, action: str, summary: str | None = None
    ) -> str | None:
        self._ensure_git_repo()
        self._run_git(["add", "objects"])
        if not self._has_staged_changes():
            return None
        title = f"{action}: {object_type}#{object_id}"
        body = str(summary or "").strip()
        message = f"{title}\n\n{body}\n" if body else f"{title}\n"
        self._run_git(
            [
                "-c",
                "user.name=Praxis Bot",
                "-c",
                "user.email=praxis@local",
                "commit",
                "-m",
                message,
            ]
        )
        return self._current_head_sha()

    def _load_manifest(self, manifest_path: Path) -> dict[str, Any]:
        if manifest_path.exists():
            try:
                content = manifest_path.read_text(encoding="utf-8")
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    def _ensure_git_repo(self) -> None:
        if (self.root / ".git").exists():
            return
        self._run_git(["init"])

    def _run_git(self, args: list[str]) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(self.root),
            check=True,
            capture_output=True,
            text=True,
        )
        return (completed.stdout or "").strip()

    def _has_staged_changes(self) -> bool:
        completed = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(self.root),
            check=True,
            capture_output=True,
            text=True,
        )
        return bool((completed.stdout or "").strip())

    def _current_head_sha(self) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.root),
                check=True,
                capture_output=True,
                text=True,
            )
            sha = (completed.stdout or "").strip()
            return sha or None
        except Exception:
            return None
