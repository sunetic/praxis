from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.sql.sqltypes import BIGINT, BOOLEAN, INTEGER, JSON, NUMERIC, TEXT, VARCHAR

from app.core.logging import fmt_kv, get_logger
from app.services.llm import LLMClient, RateLimitError, get_llm_client
from app.services.page.chart_contract import get_page_chart_contract

logger = get_logger("builder.runtime")

_PRACTICE_PREVIEW_THEME_CSS = """
:root{
  --background:#f8f9fa;
  --foreground:#1a1a1a;
  --card:#ffffff;
  --muted:#f1f3f5;
  --muted-foreground:#868e96;
  --primary:#6366f1;
  --primary-foreground:#ffffff;
  --border:#e9ecef;
  --radius:12px;
}
*{box-sizing:border-box}
html,body{width:100%;min-height:100%}
body{
  margin:0;
  background:var(--background);
  color:var(--foreground);
  font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;
  line-height:1.5;
}
main{
  max-width:1120px;
  margin:0 auto;
  padding:24px;
}
.card{
  background:var(--card);
  border:1px solid var(--border);
  border-radius:var(--radius);
  box-shadow:0 1px 2px rgba(15,23,42,.04);
}
button,.btn{
  border:1px solid transparent;
  border-radius:10px;
  padding:10px 14px;
  background:var(--primary);
  color:var(--primary-foreground);
  font:inherit;
}
input,select,textarea{
  width:100%;
  border:1px solid var(--border);
  border-radius:10px;
  padding:10px 12px;
  background:#fff;
  color:var(--foreground);
}
table{
  width:100%;
  border-collapse:collapse;
  background:#fff;
}
th,td{
  border-bottom:1px solid var(--border);
  text-align:left;
  padding:10px 12px;
}
th{
  font-weight:600;
  background:var(--muted);
}
""".strip()

_BUILD_VIEWPORT_WIDTH = 1366
_BUILD_VIEWPORT_HEIGHT = 900
_PUBLISH_VIEWPORT_WIDTH = 1440
_PUBLISH_VIEWPORT_HEIGHT = 900
_PAGE_TEMPLATE_CATALOG_VERSION = "page-template-catalog-v2"
_PAGE_TEMPLATE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "data_workbench",
        "name": "数据工作台",
        "description": "适合诊断/巡检/报表类页面。灰底白卡片层次，FilterToolbar 筛选 + 图表双列(3/5+2/5) + ListTable + PaginationFooter + DetailDrawer。",
        "structure": ["FilterToolbar", "Charts(3/5+2/5)", "ListTable+PaginationFooter", "DetailDrawer"],
        "default_primitives": ["card", "table", "button", "input", "select", "chart", "badge", "pagination", "drawer"],
        "required_primitives": ["card", "table", "pagination"],
    },
    {
        "id": "diagnostic_flow",
        "name": "诊断流程",
        "description": "适合问题清单场景。ScopeSelector/TimeRangePicker 筛选 + 结果面板 + 明细 ListTable + PaginationFooter + DetailDrawer 呈现低频详情与调试信息。",
        "structure": ["FilterToolbar(scope+time)", "ResultPanel", "ListTable+PaginationFooter", "DetailDrawer"],
        "default_primitives": ["card", "table", "button", "drawer", "input", "select", "badge", "pagination"],
        "required_primitives": ["card", "table", "drawer", "pagination"],
    },
    {
        "id": "config_form",
        "name": "配置表单",
        "description": "适合先输入参数再查询结果的场景。分组表单 + 主按钮 + 最近执行记录表。",
        "structure": ["FormSections", "PrimaryAction", "RecentRunsTable"],
        "default_primitives": ["card", "button", "form", "input", "select", "table"],
        "required_primitives": ["card", "button", "form"],
    },
)
_PAGE_TEMPLATE_INDEX: dict[str, dict[str, Any]] = {
    str(item["id"]): item for item in _PAGE_TEMPLATE_CATALOG
}
_PAGE_TEMPLATE_LEGACY_MAP: dict[str, str] = {
    "ops_overview_table": "data_workbench",
    "ops_diagnostic_with_drawer": "diagnostic_flow",
    "ops_form_and_result": "config_form",
}
_PAGE_DEFAULT_TEMPLATE_ID = str(_PAGE_TEMPLATE_CATALOG[0]["id"])
_PAGE_CHART_CONTRACT = get_page_chart_contract()
_PAGE_ALLOWED_UI_PRIMITIVES: set[str] = {
    "card",
    "table",
    "button",
    "drawer",
    "form",
    "input",
    "select",
    "tabs",
    "badge",
    "chart",
    "pagination",
}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_sqlalchemy_type(type_obj: Any) -> str:
    if isinstance(type_obj, (INTEGER, BIGINT)):
        return "integer"
    if isinstance(type_obj, NUMERIC):
        return "number"
    if isinstance(type_obj, BOOLEAN):
        return "boolean"
    if isinstance(type_obj, (VARCHAR, TEXT)):
        return "string"
    if isinstance(type_obj, JSON):
        return "object"
    return str(type_obj).lower()


def _normalize_ui_primitive_name(value: Any) -> str:
    normalized = _compact_whitespace(str(value or "")).lower()
    normalized = normalized.replace("-", "_").replace(" ", "_")
    return normalized


@dataclass(frozen=True)

class PageBuildResult:
    draft_payload: dict[str, Any]
    summary: str


@dataclass(frozen=True)
class PageBuildRunEvent:
    phase: str
    status: str
    summary: str
    created_at: str
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class PageBuildRunResult:
    run_id: str
    status: str
    phase: str
    summary: str
    draft_payload: dict[str, Any]
    events: list[PageBuildRunEvent]
    error_summary: str | None = None


class PageBuilderService:
    def __init__(self, llm_client: LLMClient | None = None):
        self.llm = llm_client or get_llm_client()

    def build_run(self, current_draft: dict[str, Any] | None, prompt: str) -> PageBuildRunResult:
        normalized_prompt = _compact_whitespace(prompt)
        run_id = f"pbr_{uuid4().hex[:16]}"
        base_draft = self._normalize_runtime_draft(current_draft)
        events: list[PageBuildRunEvent] = []
        run_started_at = time.perf_counter()
        last_elapsed_ms = 0

        def push_event(
            phase: str,
            *,
            status: str = "running",
            summary: str,
            payload: dict[str, Any] | None = None,
        ) -> None:
            nonlocal last_elapsed_ms
            elapsed_ms = int((time.perf_counter() - run_started_at) * 1000)
            phase_duration_ms = elapsed_ms - last_elapsed_ms
            last_elapsed_ms = elapsed_ms
            combined_payload = {"elapsed_ms": elapsed_ms, "phase_duration_ms": phase_duration_ms}
            if isinstance(payload, dict):
                combined_payload.update(payload)
            events.append(
                PageBuildRunEvent(
                    phase=phase,
                    status=status,
                    summary=summary,
                    created_at=_utc_now_iso(),
                    payload=combined_payload,
                )
            )

        destructive = False
        push_event(
            "intent_parsed",
            summary="已解析需求意图。",
            payload={"destructive": destructive},
        )
        try:
            runtime_payload = self._generate_runtime_with_retry(
                prompt=normalized_prompt,
                current_draft=base_draft,
                destructive=destructive,
            )
            push_event(
                "draft_planned",
                summary=str((runtime_payload.get("plan") or {}).get("goal") or "已生成草稿计划。"),
                payload={
                    "todo_count": len((runtime_payload.get("plan") or {}).get("todos") or []),
                },
            )
            push_event(
                "plan_generated",
                summary=str((runtime_payload.get("plan") or {}).get("goal") or "已生成执行计划。"),
                payload={
                    "todo_count": len((runtime_payload.get("plan") or {}).get("todos") or []),
                },
            )
            code_len = len(str((runtime_payload.get("source") or {}).get("code") or ""))
            html_len = len(str((runtime_payload.get("runtime") or {}).get("preview_html") or ""))
            push_event(
                "code_generated",
                summary="代码草稿已生成。",
                payload={"code_length": code_len, "preview_html_length": html_len},
            )
            self._validate_runtime_payload(runtime_payload, destructive=destructive)
            push_event("patch_validated", summary="方案校验通过。")
            merged_draft = self._apply_runtime_payload(
                draft=base_draft,
                runtime_payload=runtime_payload,
                prompt=normalized_prompt,
                destructive=destructive,
            )
            summary = str(merged_draft.get("meta", {}).get("summary") or "页面已更新")
            push_event("patch_applied", summary="变更已应用到草稿。")
            push_event("preview_ready", status="done", summary=summary)
            logger.info(
                "page_build_success %s",
                fmt_kv(run_id=run_id, summary=summary, code_length=code_len),
            )
            return PageBuildRunResult(
                run_id=run_id,
                status="done",
                phase="preview_ready",
                summary=summary,
                draft_payload=merged_draft,
                events=events,
            )
        except Exception as err:
            raw_error = str(err) or "构建失败，请调整需求后重试。"
            error_summary = self._presentable_error_summary(err)
            push_event(
                "failed",
                status="failed",
                summary=error_summary,
                payload={"error_type": err.__class__.__name__, "reason": raw_error},
            )
            logger.error("page_build_failed %s", fmt_kv(run_id=run_id, error=raw_error))
            return PageBuildRunResult(
                run_id=run_id,
                status="failed",
                phase="failed",
                summary=error_summary,
                draft_payload=base_draft,
                events=events,
                error_summary=error_summary,
            )

    def apply_prompt(self, current_draft: dict[str, Any] | None, prompt: str) -> PageBuildResult:
        run = self.build_run(current_draft=current_draft, prompt=prompt)
        if run.status != "done":
            raise ValueError(run.error_summary or "草稿构建失败")
        return PageBuildResult(draft_payload=run.draft_payload, summary=run.summary)

    def _presentable_error_summary(self, err: Exception) -> str:
        message = str(err) or "构建失败，请稍后重试。"
        if isinstance(err, RateLimitError):
            return "LLM 服务当前限流，请稍后重试。"
        if isinstance(err, (TimeoutError, ConnectionError, OSError, asyncio.TimeoutError)):
            return "LLM 服务暂时不可用（网络波动），请稍后重试。"
        return message

    def _is_redundant_summary(self, *, prompt: str, summary: str) -> bool:
        normalized_prompt = _compact_whitespace(prompt)
        normalized_summary = _compact_whitespace(summary)
        if not normalized_prompt or not normalized_summary:
            return False
        prompt_lower = normalized_prompt.lower()
        summary_lower = normalized_summary.lower()
        if summary_lower == prompt_lower:
            return True
        if prompt_lower in summary_lower:
            return True
        if summary_lower in prompt_lower and len(summary_lower) >= max(8, int(len(prompt_lower) * 0.6)):
            return True
        return False

    def _default_preview_html(self, message: str | None = None) -> str:
        note = str(message or "").strip()
        note_block = ""
        if note:
            escaped_note = note.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            note_block = (
                "<section class='card' style='padding:16px;color:var(--muted-foreground);'>"
                f"{escaped_note}"
                "</section>"
            )
        return (
            "<!doctype html><html><head><meta charset='utf-8' />"
            f"<style id='praxis-preview-theme'>{_PRACTICE_PREVIEW_THEME_CSS}</style>"
            "</head><body><main>"
            f"{note_block}"
            "</main></body></html>"
        )

    def _ensure_preview_theme(self, html: str) -> str:
        normalized = html.strip()
        if not normalized:
            return self._default_preview_html()
        if "<html" not in normalized.lower():
            return self._default_preview_html(normalized)
        if not re.search(r"<head\b", normalized, flags=re.IGNORECASE):
            normalized = re.sub(
                r"<html([^>]*)>",
                r"<html\1><head></head>",
                normalized,
                count=1,
                flags=re.IGNORECASE,
            )
        if not re.search(r"<meta[^>]+charset", normalized, flags=re.IGNORECASE):
            normalized = re.sub(
                r"<head([^>]*)>",
                r"<head\1><meta charset='utf-8' />",
                normalized,
                count=1,
                flags=re.IGNORECASE,
            )
        if "praxis-preview-theme" not in normalized.lower():
            normalized = re.sub(
                r"</head\s*>",
                f"<style id='praxis-preview-theme'>{_PRACTICE_PREVIEW_THEME_CSS}</style></head>",
                normalized,
                count=1,
                flags=re.IGNORECASE,
            )
        return normalized

    def _normalize_runtime_draft(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        draft = copy.deepcopy(raw) if isinstance(raw, dict) else {}
        config = draft.get("config") if isinstance(draft.get("config"), dict) else {}
        source = draft.get("source") if isinstance(draft.get("source"), dict) else {}
        runtime = draft.get("runtime") if isinstance(draft.get("runtime"), dict) else {}
        meta = draft.get("meta") if isinstance(draft.get("meta"), dict) else {}
        history = meta.get("history") if isinstance(meta.get("history"), list) else []
        plan = meta.get("plan") if isinstance(meta.get("plan"), dict) else {}
        todos = plan.get("todos") if isinstance(plan.get("todos"), list) else []
        template_meta = meta.get("template") if isinstance(meta.get("template"), dict) else {}
        template_id = str(template_meta.get("id") or _PAGE_DEFAULT_TEMPLATE_ID).strip()
        if template_id not in _PAGE_TEMPLATE_INDEX:
            template_id = _PAGE_DEFAULT_TEMPLATE_ID
        defaults = _PAGE_TEMPLATE_INDEX.get(template_id, {}).get("default_primitives") or []
        normalized_primitives: list[str] = []
        for item in (template_meta.get("ui_primitives") or []):
            primitive = _normalize_ui_primitive_name(item)
            if primitive in _PAGE_ALLOWED_UI_PRIMITIVES and primitive not in normalized_primitives:
                normalized_primitives.append(primitive)
        if not normalized_primitives:
            normalized_primitives = [
                _normalize_ui_primitive_name(item)
                for item in defaults
                if _normalize_ui_primitive_name(item) in _PAGE_ALLOWED_UI_PRIMITIVES
            ]
        if not normalized_primitives:
            normalized_primitives = ["card", "table", "button"]
        preview_html = str(runtime.get("preview_html") or "")
        if not preview_html:
            preview_html = self._default_preview_html()
        preview_html = self._ensure_preview_theme(preview_html)
        return {
            "version": "page-runtime-v2",
            "config": {
                "title": str(config.get("title") or ""),
                "description": str(config.get("description") or ""),
            },
            "source": {
                "language": str(source.get("language") or "tsx"),
                "code": str(source.get("code") or ""),
            },
            "runtime": {
                "framework": str(runtime.get("framework") or "html"),
                "preview_html": preview_html,
            },
            "meta": {
                "updated_at": str(meta.get("updated_at") or ""),
                "last_prompt": str(meta.get("last_prompt") or ""),
                "summary": str(meta.get("summary") or ""),
                "plan": {
                    "goal": str(plan.get("goal") or ""),
                    "todos": [str(item) for item in todos if str(item).strip()],
                },
                "template": {
                    "id": template_id,
                    "ui_primitives": normalized_primitives,
                    "intake_assumptions": [
                        str(item).strip()
                        for item in (template_meta.get("intake_assumptions") or [])
                        if str(item).strip()
                    ],
                    "catalog_version": _PAGE_TEMPLATE_CATALOG_VERSION,
                },
                "history": [item for item in history if isinstance(item, dict)],
            },
        }

    def _generate_runtime_with_retry(
        self,
        *,
        prompt: str,
        current_draft: dict[str, Any],
        destructive: bool,
    ) -> dict[str, Any]:
        first = self._normalize_runtime_payload(
            self._generate_runtime_payload(
                prompt=prompt,
                current_draft=current_draft,
                destructive=destructive,
                previous_error=None,
            )
        )
        try:
            self._validate_runtime_payload(first, destructive=destructive)
            return first
        except Exception as err:
            retry = self._normalize_runtime_payload(
                self._generate_runtime_payload(
                    prompt=prompt,
                    current_draft=current_draft,
                    destructive=destructive,
                    previous_error=str(err),
                )
            )
            self._validate_runtime_payload(retry, destructive=destructive)
            return retry

    def _generate_runtime_payload(
        self,
        *,
        prompt: str,
        current_draft: dict[str, Any],
        destructive: bool,
        previous_error: str | None,
    ) -> dict[str, Any]:
        payload = {
            "user_prompt": prompt,
            "destructive_intent_detected": destructive,
            "current_state": self._project_runtime_for_prompt(current_draft),
            "design_system": {
                "layout": {
                    "page_max_width": "1120px",
                    "container_padding": "24px",
                    "spacing_scale": ["8px", "12px", "16px", "24px", "32px"],
                    "build_canvas": {
                        "width": _BUILD_VIEWPORT_WIDTH,
                        "height": _BUILD_VIEWPORT_HEIGHT,
                    },
                    "publish_viewport": {
                        "width": _PUBLISH_VIEWPORT_WIDTH,
                        "height": _PUBLISH_VIEWPORT_HEIGHT,
                    },
                    "mobile_viewport": {
                        "width": 390,
                        "height": 844,
                    },
                },
                "tokens": {
                    "background": "#f8f9fa",
                    "foreground": "#1a1a1a",
                    "card": "#ffffff",
                    "muted": "#f1f3f5",
                    "muted_foreground": "#868e96",
                    "primary": "#6366f1",
                    "border": "#e9ecef",
                    "radius": "12px",
                },
                "typography": {
                    "font_family": "Inter, system-ui, -apple-system, sans-serif",
                    "title_weight": 600,
                    "body_weight": 400,
                },
                "host_context": {
                    "surface_type": "workbench-content",
                    "chrome_owned_by_host_app": True,
                    "page_should_focus_on_business_content": True,
                },
                "component_profile": {
                    "preferred_components": [
                        "summary cards",
                        "data tables",
                        "forms",
                        "filters",
                        "metric blocks",
                        "timeline/list",
                    ],
                    "interaction_tone": "professional, dense-enough for real usage",
                    "anti_patterns": [
                        "toy demo style",
                        "full-screen hero section",
                        "neon colors",
                        "overly decorative gradients",
                    ],
                },
                "template_catalog": {
                    "version": _PAGE_TEMPLATE_CATALOG_VERSION,
                    "default_template": _PAGE_DEFAULT_TEMPLATE_ID,
                    "templates": _PAGE_TEMPLATE_CATALOG,
                    "allowed_ui_primitives": sorted(_PAGE_ALLOWED_UI_PRIMITIVES),
                },
                "chart_contract": _PAGE_CHART_CONTRACT,
                "style_rules": [
                    "Match host style: clean SaaS dashboard, light surface, soft borders, subtle shadows.",
                    "This is a standalone business page rendered inside an existing app shell; do NOT recreate app nav/sidebar/header.",
                    "Page layout must follow gray-background + white-card elevation pattern: each content block is a white card (rounded-xl, shadow-sm) on a gray (#F5F6FA) page background.",
                    "No page-level h1 title or subtitle — page title is carried by the sidebar nav; the main area must not repeat it.",
                    "Use 8px spacing grid (4/8/12/16/20/24/32px values only). Major sections separated by space-y-6, inner sections by space-y-4.",
                    "All colors must use design tokens (var(--primary), var(--border), etc.), no hardcoded hex/rgb except in chart SVG strokes.",
                    "FilterToolbar at top of page for high-frequency filters; low-frequency items fold away.",
                    "StatCards (KPI metrics) in 4-column grid with accent color mapping, optional but preferred for dashboard templates.",
                    "Charts in dual-column layout (3/5 + 2/5 width) with entry animation (animate-in fade-in slide-in-from-bottom-1 duration-500).",
                    "Tabular data must use semantic <table><thead><tbody> with ListTable pattern + PaginationFooter.",
                    "Every list must have 4-state coverage: loading (skeleton rows), empty (icon + text), error (recoverable hint), loaded.",
                    "Detail panels use Drawer with single-line header (title + close only), no stacked meta-info.",
                    "Card entry animation: translate-y-3 → 0, opacity 0 → 1, 300ms + stagger 80ms between cards.",
                    "Choose exactly one layout template from template_catalog.templates.",
                    "When layout details are missing, select template_catalog.default_template and continue with assumptions.",
                    "ui_primitives must come from template_catalog.allowed_ui_primitives only.",
                    "When a chart is required, choose component and props from chart_contract only.",
                ],
            },
            "preview_html_theme_scaffold": _PRACTICE_PREVIEW_THEME_CSS,
            "schema": {
                "intent_summary": "string",
                "plan": {"goal": "string", "todos": ["string"]},
                "layout_template": "string",
                "ui_primitives": ["string"],
                "intake_assumptions": ["string"],
                "config": {"title": "string", "description": "string"},
                "source": {"language": "tsx", "code": "string"},
                "runtime": {"framework": "html", "preview_html": "string"},
            },
            "constraints": [
                "You must respond with JSON only.",
                "Do not use widget/operations DSL.",
                "Output executable preview_html that can run standalone in iframe srcdoc.",
                "Output source.code as React TSX page source for persistence/release.",
                "The generated page must visually blend with the host app style system.",
                "Use the provided design_system tokens and spacing scale.",
                "Use preview_html_theme_scaffold in <style id='praxis-preview-theme'> inside <head>.",
                "Keep existing user intent and preserve useful page structure unless destructive_intent_detected is true.",
                "preview_html should include complete html/body and visible result.",
                "Do not apply global scale/zoom transforms to make the page smaller; output natural full-size layout.",
                "Ensure first-screen composition looks balanced at publish_viewport and remains usable on mobile_viewport.",
                "Do not use external CSS/JS CDNs; keep preview self-contained.",
                "Source TSX should prioritize existing shadcn-like primitives (Card/Table/Button/Drawer/Form) when they fit the chosen template.",
            ],
            "previous_error": previous_error,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a Page Runtime Builder. "
                    "Generate direct runtime artifacts for page building. "
                    "Prioritize production-ready UI quality and visual consistency with the host product. "
                    "Use semantic HTML and business-oriented component composition, not demo widgets. "
                    "Return JSON only with keys: intent_summary, plan, layout_template, ui_primitives, intake_assumptions, config, source, runtime."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        raw = asyncio.run(self._call_llm_for_json(messages=messages))
        return self._parse_json_patch(raw)

    def _normalize_runtime_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = copy.deepcopy(payload)
        normalized["intent_summary"] = str(normalized.get("intent_summary") or "").strip()
        plan = normalized.get("plan")
        if not isinstance(plan, dict):
            plan = {}
        todos = plan.get("todos")
        normalized["plan"] = {
            "goal": str(plan.get("goal") or normalized["intent_summary"] or "").strip(),
            "todos": [str(item).strip() for item in todos if str(item).strip()]
            if isinstance(todos, list)
            else [],
        }
        config = normalized.get("config")
        if not isinstance(config, dict):
            config = {}
        normalized["config"] = {
            "title": str(config.get("title") or "").strip(),
            "description": str(config.get("description") or "").strip(),
        }
        source = normalized.get("source")
        if not isinstance(source, dict):
            source = {}
        normalized["source"] = {
            "language": str(source.get("language") or "tsx").strip(),
            "code": str(source.get("code") or "").strip(),
        }
        runtime = normalized.get("runtime")
        if not isinstance(runtime, dict):
            runtime = {}
        preview_html = self._ensure_preview_theme(str(runtime.get("preview_html") or "").strip())
        normalized["runtime"] = {
            "framework": str(runtime.get("framework") or "html").strip(),
            "preview_html": preview_html,
        }
        layout_template = str(normalized.get("layout_template") or "").strip()
        if layout_template not in _PAGE_TEMPLATE_INDEX:
            layout_template = _PAGE_DEFAULT_TEMPLATE_ID
        template_defaults = _PAGE_TEMPLATE_INDEX.get(layout_template, {}).get("default_primitives") or []
        normalized_primitives: list[str] = []
        for item in (normalized.get("ui_primitives") or []):
            primitive = _normalize_ui_primitive_name(item)
            if primitive in _PAGE_ALLOWED_UI_PRIMITIVES and primitive not in normalized_primitives:
                normalized_primitives.append(primitive)
        if not normalized_primitives:
            normalized_primitives = [
                _normalize_ui_primitive_name(item)
                for item in template_defaults
                if _normalize_ui_primitive_name(item) in _PAGE_ALLOWED_UI_PRIMITIVES
            ]
        required_primitives = _PAGE_TEMPLATE_INDEX.get(layout_template, {}).get("required_primitives") or []
        for item in required_primitives:
            primitive = _normalize_ui_primitive_name(item)
            if primitive in _PAGE_ALLOWED_UI_PRIMITIVES and primitive not in normalized_primitives:
                normalized_primitives.append(primitive)
        normalized["layout_template"] = layout_template
        normalized["ui_primitives"] = normalized_primitives
        assumptions: list[str] = []
        for item in (normalized.get("intake_assumptions") or []):
            value = str(item).strip()
            if value:
                assumptions.append(value)
        normalized["intake_assumptions"] = assumptions
        return normalized

    def _validate_runtime_payload(self, payload: dict[str, Any], *, destructive: bool) -> None:
        plan = payload.get("plan")
        if not isinstance(plan, dict):
            raise ValueError("runtime.plan 必须是对象")
        if not str(plan.get("goal") or "").strip():
            raise ValueError("runtime.plan.goal 不能为空")
        todos = plan.get("todos")
        if not isinstance(todos, list):
            raise ValueError("runtime.plan.todos 必须是数组")
        if not all(isinstance(item, str) and item.strip() for item in todos):
            raise ValueError("runtime.plan.todos 必须是非空字符串数组")

        source = payload.get("source")
        if not isinstance(source, dict):
            raise ValueError("runtime.source 必须是对象")
        if not str(source.get("code") or "").strip():
            raise ValueError("runtime.source.code 不能为空")

        runtime = payload.get("runtime")
        if not isinstance(runtime, dict):
            raise ValueError("runtime.runtime 必须是对象")
        html = str(runtime.get("preview_html") or "").strip()
        if not html:
            raise ValueError("runtime.runtime.preview_html 不能为空")
        if "<html" not in html.lower():
            raise ValueError("runtime.runtime.preview_html 必须包含完整 HTML")

        config = payload.get("config")
        if not isinstance(config, dict):
            raise ValueError("runtime.config 必须是对象")
        template_id = str(payload.get("layout_template") or "").strip()
        if not template_id:
            raise ValueError("runtime.layout_template 不能为空")
        if template_id in _PAGE_TEMPLATE_LEGACY_MAP:
            template_id = _PAGE_TEMPLATE_LEGACY_MAP[template_id]
            payload["layout_template"] = template_id
        if template_id not in _PAGE_TEMPLATE_INDEX:
            raise ValueError("runtime.layout_template 不在模板目录中")
        ui_primitives = payload.get("ui_primitives")
        if not isinstance(ui_primitives, list):
            raise ValueError("runtime.ui_primitives 必须是数组")
        normalized_primitives: list[str] = []
        for item in ui_primitives:
            primitive = _normalize_ui_primitive_name(item)
            if not primitive:
                continue
            if primitive not in _PAGE_ALLOWED_UI_PRIMITIVES:
                raise ValueError(f"runtime.ui_primitives 包含未支持组件: {primitive}")
            if primitive not in normalized_primitives:
                normalized_primitives.append(primitive)
        if not normalized_primitives:
            raise ValueError("runtime.ui_primitives 至少包含一个组件")
        required_primitives = _PAGE_TEMPLATE_INDEX.get(template_id, {}).get("required_primitives") or []
        missing_required = [
            _normalize_ui_primitive_name(item)
            for item in required_primitives
            if _normalize_ui_primitive_name(item) not in normalized_primitives
        ]
        if missing_required:
            raise ValueError(
                "runtime.ui_primitives 缺少模板必需组件: " + ", ".join(missing_required)
            )
        assumptions = payload.get("intake_assumptions")
        if assumptions is not None and not isinstance(assumptions, list):
            raise ValueError("runtime.intake_assumptions 必须是数组")
        if isinstance(assumptions, list) and not all(
            isinstance(item, str) and item.strip() for item in assumptions
        ):
            raise ValueError("runtime.intake_assumptions 必须是非空字符串数组")

    def _apply_runtime_payload(
        self,
        *,
        draft: dict[str, Any],
        runtime_payload: dict[str, Any],
        prompt: str,
        destructive: bool,
    ) -> dict[str, Any]:
        next_draft = copy.deepcopy(draft)
        config = runtime_payload.get("config") if isinstance(runtime_payload.get("config"), dict) else {}
        source = runtime_payload.get("source") if isinstance(runtime_payload.get("source"), dict) else {}
        runtime = runtime_payload.get("runtime") if isinstance(runtime_payload.get("runtime"), dict) else {}
        summary = str(runtime_payload.get("intent_summary") or "页面已更新")
        if self._is_redundant_summary(prompt=prompt, summary=summary):
            summary = "页面已更新，请查看预览结果。"
        next_draft["version"] = "page-runtime-v2"
        next_draft["config"] = {
            "title": str(config.get("title") or next_draft.get("config", {}).get("title") or ""),
            "description": str(config.get("description") or ""),
        }
        next_draft["source"] = {
            "language": str(source.get("language") or "tsx"),
            "code": str(source.get("code") or ""),
        }
        next_draft["runtime"] = {
            "framework": str(runtime.get("framework") or "html"),
            "preview_html": str(runtime.get("preview_html") or ""),
        }
        meta = next_draft.get("meta") if isinstance(next_draft.get("meta"), dict) else {}
        history = meta.get("history") if isinstance(meta.get("history"), list) else []
        plan = runtime_payload.get("plan") if isinstance(runtime_payload.get("plan"), dict) else {}
        template_id = str(runtime_payload.get("layout_template") or _PAGE_DEFAULT_TEMPLATE_ID).strip()
        if template_id not in _PAGE_TEMPLATE_INDEX:
            template_id = _PAGE_DEFAULT_TEMPLATE_ID
        ui_primitives = runtime_payload.get("ui_primitives")
        normalized_primitives: list[str] = []
        if isinstance(ui_primitives, list):
            for item in ui_primitives:
                primitive = _normalize_ui_primitive_name(item)
                if primitive in _PAGE_ALLOWED_UI_PRIMITIVES and primitive not in normalized_primitives:
                    normalized_primitives.append(primitive)
        if not normalized_primitives:
            defaults = _PAGE_TEMPLATE_INDEX.get(template_id, {}).get("default_primitives") or []
            normalized_primitives = [
                _normalize_ui_primitive_name(item)
                for item in defaults
                if _normalize_ui_primitive_name(item) in _PAGE_ALLOWED_UI_PRIMITIVES
            ]
        required_primitives = _PAGE_TEMPLATE_INDEX.get(template_id, {}).get("required_primitives") or []
        for item in required_primitives:
            primitive = _normalize_ui_primitive_name(item)
            if primitive in _PAGE_ALLOWED_UI_PRIMITIVES and primitive not in normalized_primitives:
                normalized_primitives.append(primitive)
        intake_assumptions = runtime_payload.get("intake_assumptions")
        normalized_assumptions = (
            [str(item).strip() for item in intake_assumptions if str(item).strip()]
            if isinstance(intake_assumptions, list)
            else []
        )
        next_draft["meta"] = {
            "updated_at": _utc_now_iso(),
            "last_prompt": prompt,
            "summary": summary,
            "plan": {
                "goal": str(plan.get("goal") or summary),
                "todos": [str(item) for item in plan.get("todos", [])] if isinstance(plan.get("todos"), list) else [],
            },
            "template": {
                "id": template_id,
                "ui_primitives": normalized_primitives,
                "intake_assumptions": normalized_assumptions,
                "catalog_version": _PAGE_TEMPLATE_CATALOG_VERSION,
            },
            "history": (
                history
                + [
                    {
                        "prompt": prompt,
                        "summary": summary,
                        "template_id": template_id,
                        "created_at": _utc_now_iso(),
                    }
                ]
            )[-10:],
        }
        return next_draft

    def _project_runtime_for_prompt(self, draft: dict[str, Any]) -> dict[str, Any]:
        config = draft.get("config") if isinstance(draft.get("config"), dict) else {}
        source = draft.get("source") if isinstance(draft.get("source"), dict) else {}
        runtime = draft.get("runtime") if isinstance(draft.get("runtime"), dict) else {}
        meta = draft.get("meta") if isinstance(draft.get("meta"), dict) else {}
        template_meta = meta.get("template") if isinstance(meta.get("template"), dict) else {}
        template_id = str(template_meta.get("id") or _PAGE_DEFAULT_TEMPLATE_ID).strip()
        if template_id not in _PAGE_TEMPLATE_INDEX:
            template_id = _PAGE_DEFAULT_TEMPLATE_ID
        ui_primitives: list[str] = []
        for item in (template_meta.get("ui_primitives") or []):
            primitive = _normalize_ui_primitive_name(item)
            if primitive in _PAGE_ALLOWED_UI_PRIMITIVES and primitive not in ui_primitives:
                ui_primitives.append(primitive)
        if not ui_primitives:
            defaults = _PAGE_TEMPLATE_INDEX.get(template_id, {}).get("default_primitives") or []
            ui_primitives = [
                _normalize_ui_primitive_name(item)
                for item in defaults
                if _normalize_ui_primitive_name(item) in _PAGE_ALLOWED_UI_PRIMITIVES
            ]
        return {
            "version": str(draft.get("version") or ""),
            "config": {
                "title": str(config.get("title") or ""),
                "description": str(config.get("description") or ""),
            },
            "source": {
                "language": str(source.get("language") or ""),
                "code": str(source.get("code") or ""),
            },
            "runtime": {
                "framework": str(runtime.get("framework") or ""),
                "preview_html": str(runtime.get("preview_html") or ""),
            },
            "template": {
                "id": template_id,
                "ui_primitives": ui_primitives,
                "intake_assumptions": [
                    str(item).strip()
                    for item in (template_meta.get("intake_assumptions") or [])
                    if str(item).strip()
                ],
                "catalog_version": _PAGE_TEMPLATE_CATALOG_VERSION,
            },
        }

    async def _call_llm_for_json(self, *, messages: list[dict[str, str]]) -> str:
        response: dict[str, Any] | None = None
        async for chunk in self.llm.chat(
            messages=messages,
            tools=None,
            stream=False,
            temperature=0.0,
            response_format={"type": "json_object"},
        ):
            response = chunk
            break
        if response is None:
            raise ValueError("LLM 返回为空")
        content = (((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not content:
            raise ValueError("LLM 未返回结构化 patch")
        return content

    def _parse_json_patch(self, raw: str) -> dict[str, Any]:
        text = raw.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        try:
            data = json.loads(text)
        except Exception as err:
            raise ValueError(f"LLM patch 不是合法 JSON: {err}") from err
        if not isinstance(data, dict):
            raise ValueError("LLM patch 必须是 JSON 对象")
        return data

