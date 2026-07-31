from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

_MAX_EXCERPT_CHARS = 32000
_MAX_VISIBLE_TEXTS = 24
_MAX_ITEMS = 12
_MAX_DRAWER_CONTEXT_CHARS = 1800
_MAX_DESIGN_SPEC_CHARS = 8000
_DESIGN_SPEC_RELATIVE_PATH = ".cursor/rules/frontend-design.mdc"
_DEFAULT_REVIEW_FOCUS = ("页面目标漂移", "信息噪音", "内部术语泄露")
_DEFAULT_ANTI_GOALS = (
    "暴露内部实现术语",
    "把低频信息或调试信息常驻放进主区",
    "加入与页面主流程无关的噪音功能",
)
_COMPONENT_HINT_CANDIDATES = (
    "WorkbenchPage",
    "FilterToolbar",
    "TimeRangePicker",
    "DetailDrawer",
    "Drawer",
    "Dialog",
    "Table",
    "Button",
    "Input",
    "Select",
    "Tabs",
    "Card",
    "ListTable",
    "PaginationFooter",
    "StatsOverviewCards",
    "SceneAgentChatShell",
    "Badge",
)


@dataclass(frozen=True)
class PageSemanticReviewConfig:
    enabled: bool
    page_purpose: str
    primary_workflow: list[str]
    anti_goals: list[str]
    review_focus: list[str]
    observations: list[str]


@dataclass(frozen=True)
class PageReviewEvidencePacket:
    page_purpose: str
    primary_workflow: list[str]
    anti_goals: list[str]
    review_focus: list[str]
    observations: list[str]
    implementation_evidence: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def build_default_page_semantic_review_payload(
    *,
    prompt: str,
    conversation_context: str = "",
    raw_semantic_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = raw_semantic_review if isinstance(raw_semantic_review, dict) else {}
    page_purpose = _normalize_text(str(raw.get("page_purpose") or "")) or _derive_page_purpose(
        prompt
    )
    primary_workflow = _normalize_string_list(
        raw.get("primary_workflow")
    ) or _derive_primary_workflow(
        prompt=prompt,
        conversation_context=conversation_context,
    )
    anti_goals = _normalize_string_list(raw.get("anti_goals")) or list(_DEFAULT_ANTI_GOALS)
    review_focus = _normalize_string_list(raw.get("review_focus")) or list(_DEFAULT_REVIEW_FOCUS)
    observations = _normalize_string_list(raw.get("observations"))
    return {
        "enabled": True,
        "page_purpose": page_purpose,
        "primary_workflow": primary_workflow,
        "anti_goals": anti_goals,
        "review_focus": review_focus,
        "observations": observations,
    }


_FRONTEND_PAGE_PATTERN = re.compile(
    r"frontend/src/.*(?<!\.test)(?<!\.spec)\.(tsx|jsx)$", re.IGNORECASE
)


def _is_frontend_page(file_path: Path) -> bool:
    """Return True when the file looks like a frontend page/component."""
    return bool(_FRONTEND_PAGE_PATTERN.search(str(file_path)))


def _load_design_spec(repo_root: str | Path) -> str:
    """Load the frontend design spec from the rules file, stripping YAML frontmatter."""
    if not repo_root:
        return ""
    spec_path = Path(repo_root) / _DESIGN_SPEC_RELATIVE_PATH
    if not spec_path.is_file():
        return ""
    raw = spec_path.read_text(encoding="utf-8").strip()
    # Strip YAML frontmatter (--- ... ---)
    if raw.startswith("---"):
        end = raw.find("---", 3)
        if end >= 0:
            raw = raw[end + 3 :].strip()
    if len(raw) > _MAX_DESIGN_SPEC_CHARS:
        raw = raw[: _MAX_DESIGN_SPEC_CHARS - 3].rstrip() + "..."
    return raw


def build_repo_page_file_review_evidence(
    *,
    file_path: str | Path,
    prompt: str,
    conversation_context: str = "",
    raw_semantic_review: dict[str, Any] | None = None,
    repo_root: str | Path = "",
) -> PageReviewEvidencePacket:
    path_obj = Path(file_path)
    source_code = path_obj.read_text(encoding="utf-8")
    review_source = _build_repo_review_scope_source(source_code)
    semantic_review = build_default_page_semantic_review_payload(
        prompt=prompt,
        conversation_context=conversation_context,
        raw_semantic_review=raw_semantic_review,
    )
    primary_workflow = _normalize_string_list(semantic_review.get("primary_workflow"))
    if "SceneAgentChatShell" in review_source and not any(
        token in step.lower() for step in primary_workflow for token in ("chat", "对话")
    ):
        primary_workflow.append("按需进入对话补充分析")
    design_spec = _load_design_spec(repo_root) if _is_frontend_page(path_obj) else ""
    child_component_texts = (
        _extract_child_component_texts(source_code, path_obj, repo_root) if repo_root else {}
    )
    observations = _normalize_string_list(semantic_review.get("observations"))
    observations.extend(_derive_child_observations(child_component_texts))
    page_purpose = str(semantic_review.get("page_purpose") or "")
    if _looks_like_task_request(page_purpose):
        page_purpose = _derive_page_purpose_from_source(review_source, child_component_texts)
    verified_patterns = _verify_design_patterns(source_code, child_component_texts)
    implementation_evidence = {
        "file_path": str(path_obj),
        "verified_patterns": verified_patterns,
        "visible_texts": _extract_visible_texts_from_source(review_source),
        "heading_texts": _extract_heading_texts_from_source(review_source),
        "control_counts": _extract_control_counts_from_source(review_source),
        "component_hints": _extract_component_hints(review_source),
        "child_component_texts": child_component_texts,
        "drawer_copy_blocks": _extract_named_const_blocks(
            source_code,
            names=("drawerTitle", "drawerDescription", "drawerContextText"),
        ),
        "drawer_layout_windows": _extract_source_windows(
            source_code,
            markers=(
                "<Drawer open=",
                "<DrawerBody",
                'drawerMode === "chat"',
                'drawerMode === "detail"',
            ),
            max_chars=_MAX_DRAWER_CONTEXT_CHARS,
        ),
        "source_excerpt": _truncate(review_source),
        "preview_excerpt": "",
        "design_spec": design_spec,
    }
    return PageReviewEvidencePacket(
        page_purpose=page_purpose,
        primary_workflow=primary_workflow[:_MAX_ITEMS],
        anti_goals=_normalize_string_list(semantic_review.get("anti_goals")),
        review_focus=_normalize_string_list(semantic_review.get("review_focus")),
        observations=observations[:_MAX_ITEMS],
        implementation_evidence=implementation_evidence,
    )


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.visible_texts: list[str] = []
        self.heading_texts: list[str] = []
        self.control_counts = {
            "button": 0,
            "input": 0,
            "select": 0,
            "table": 0,
            "dialog": 0,
            "drawer": 0,
        }
        self._active_heading: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = str(tag or "").strip().lower()
        if normalized in {"h1", "h2", "h3"}:
            self._active_heading = normalized
        if normalized in self.control_counts:
            self.control_counts[normalized] += 1
        if normalized == "div":
            for _, value in attrs:
                classes = str(value or "").lower()
                if "dialog" in classes:
                    self.control_counts["dialog"] += 1
                if "drawer" in classes:
                    self.control_counts["drawer"] += 1

    def handle_endtag(self, tag: str) -> None:
        if str(tag or "").strip().lower() in {"h1", "h2", "h3"}:
            self._active_heading = None

    def handle_data(self, data: str) -> None:
        text = _normalize_text(data)
        if not text:
            return
        if text not in self.visible_texts:
            self.visible_texts.append(text)
        if self._active_heading and text not in self.heading_texts:
            self.heading_texts.append(text)


def normalize_page_semantic_review_config(
    orchestration: dict[str, Any] | None,
) -> PageSemanticReviewConfig | None:
    if not isinstance(orchestration, dict):
        return None
    raw = orchestration.get("semantic_review")
    if not isinstance(raw, dict):
        return None
    enabled = bool(raw.get("enabled"))
    if not enabled:
        return None
    return PageSemanticReviewConfig(
        enabled=True,
        page_purpose=str(raw.get("page_purpose") or "").strip(),
        primary_workflow=_normalize_string_list(raw.get("primary_workflow")),
        anti_goals=_normalize_string_list(raw.get("anti_goals")),
        review_focus=_normalize_string_list(raw.get("review_focus")),
        observations=_normalize_string_list(raw.get("observations")),
    )


def build_page_review_evidence(
    *, page: Any, config: PageSemanticReviewConfig
) -> PageReviewEvidencePacket:
    draft_payload = (
        page.draft_payload if isinstance(getattr(page, "draft_payload", None), dict) else {}
    )
    source = draft_payload.get("source") if isinstance(draft_payload.get("source"), dict) else {}
    runtime = draft_payload.get("runtime") if isinstance(draft_payload.get("runtime"), dict) else {}
    source_code = str(source.get("code") or "").strip()
    preview_html = str(runtime.get("preview_html") or "").strip()
    parser = _VisibleTextParser()
    if preview_html:
        parser.feed(preview_html)

    implementation_evidence = {
        "page_name": str(getattr(page, "name", "") or "").strip(),
        "visible_texts": parser.visible_texts[:_MAX_VISIBLE_TEXTS],
        "heading_texts": parser.heading_texts[:_MAX_ITEMS],
        "control_counts": parser.control_counts,
        "component_hints": _extract_component_hints(source_code),
        "source_excerpt": _truncate(source_code),
        "preview_excerpt": _truncate(preview_html),
    }
    return PageReviewEvidencePacket(
        page_purpose=config.page_purpose,
        primary_workflow=config.primary_workflow,
        anti_goals=config.anti_goals,
        review_focus=config.review_focus,
        observations=config.observations,
        implementation_evidence=implementation_evidence,
    )


def _extract_component_hints(source_code: str) -> list[str]:
    hints: list[str] = []
    for name in _COMPONENT_HINT_CANDIDATES:
        if name in source_code:
            hints.append(name)
    return hints[:_MAX_ITEMS]


def _extract_visible_texts_from_source(source_code: str) -> list[str]:
    matches = re.findall(r">([^<>{}\n][^<>{}]*)<", source_code, flags=re.MULTILINE)
    texts: list[str] = []
    for item in matches:
        normalized = _normalize_text(item)
        if not normalized:
            continue
        if re.fullmatch(r"[\W_]+", normalized):
            continue
        if _looks_like_code_fragment(normalized):
            continue
        if normalized not in texts:
            texts.append(normalized)
    return texts[:_MAX_VISIBLE_TEXTS]


def _extract_heading_texts_from_source(source_code: str) -> list[str]:
    matches = re.findall(
        r"<h[1-3][^>]*>([^<>{}\n][^<>{}]*)</h[1-3]>", source_code, flags=re.IGNORECASE
    )
    texts: list[str] = []
    for item in matches:
        normalized = _normalize_text(item)
        if normalized and normalized not in texts:
            texts.append(normalized)
    return texts[:_MAX_ITEMS]


def _extract_control_counts_from_source(source_code: str) -> dict[str, int]:
    lowered = source_code.lower()
    drawer_count = (1 if re.search(r"<Drawer(?:\s|>)", source_code) else 0) + source_code.count(
        "<DetailDrawer"
    )
    return {
        "button": lowered.count("<button") + source_code.count("<Button"),
        "input": lowered.count("<input") + source_code.count("<Input"),
        "select": lowered.count("<select") + source_code.count("<Select"),
        "table": lowered.count("<table") + source_code.count("<Table"),
        "dialog": source_code.count("<Dialog"),
        "drawer": drawer_count,
    }


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        normalized = _normalize_text(str(item or ""))
        if normalized:
            items.append(normalized)
    return items[:_MAX_ITEMS]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _derive_page_purpose(prompt: str) -> str:
    normalized = _normalize_text(prompt)
    if not normalized:
        return "让页面围绕当前需求的核心任务服务用户决策。"
    return normalized[:180]


def _derive_primary_workflow(*, prompt: str, conversation_context: str) -> list[str]:
    text = f"{prompt}\n{conversation_context}".lower()
    steps: list[str] = []
    if any(token in text for token in ("筛选", "搜索", "过滤", "查找", "定位")):
        steps.append("筛选或定位目标对象")
    if any(token in text for token in ("列表", "table", "清单")):
        steps.append("浏览列表并识别需要继续查看的对象")
    if any(token in text for token in ("详情", "抽屉", "drawer", "展开")):
        steps.append("进入详情查看关键信息")
    if any(token in text for token in ("诊断", "分析", "explain", "解读")):
        steps.append("查看分析结果并继续判断下一步")
    if any(token in text for token in ("chat", "对话")):
        steps.append("按需进入对话补充分析")
    if not steps:
        steps = ["进入页面", "完成页面核心任务", "查看结果反馈"]
    unique_steps: list[str] = []
    for step in steps:
        if step not in unique_steps:
            unique_steps.append(step)
    return unique_steps[:_MAX_ITEMS]


_TASK_REQUEST_MARKERS = re.compile(
    r"缺少|缺失|没有|不足|修复|改进|新增|添加|增加|问题|bug|fix|"
    r"空白|消失|不见了|丢失|失效|不显示|不工作|不生效|报错|异常|"
    r"以前.{0,6}现在|broken|blank|empty|missing|regression",
    re.IGNORECASE,
)


def _looks_like_task_request(text: str) -> bool:
    """Return True when text reads like a change request rather than a page purpose description."""
    return bool(_TASK_REQUEST_MARKERS.search(text))


def _derive_page_purpose_from_source(
    review_source: str,
    child_component_texts: dict[str, Any],
) -> str:
    """Infer the page's actual purpose from its source code structure."""
    hints: list[str] = []
    lowered = review_source.lower()
    if "workbenchpage" in lowered:
        hints.append("工作台式页面")
    if "filtertoolbar" in lowered:
        hints.append("支持筛选")
    if "listtable" in lowered or "<table" in lowered:
        hints.append("包含数据表格")
    if "<drawer" in lowered:
        hints.append("带有详情抽屉")
    if "sceneagentchatshell" in lowered:
        hints.append("集成对话分析")
    for name in child_component_texts:
        if "overview" in name.lower() or "card" in name.lower():
            hints.append(f"通过 {name} 展示概览卡片与配置检测")
    if not hints:
        return "让页面围绕当前需求的核心任务服务用户决策。"
    return "页面功能: " + "、".join(hints) + "。审查时以源码实现为准，不以 task request 推断缺失。"


def _verify_design_patterns(
    source_code: str, child_component_texts: dict[str, Any]
) -> dict[str, bool]:
    """Pre-verify key design patterns by scanning source code. Results are authoritative."""
    all_sources = source_code
    for info in child_component_texts.values():
        all_sources += "\n" + str(info.get("source_excerpt") or "")
    has_tabular_data_intent = any(
        t in all_sources
        for t in ("ListTable", "<Table", "<table", "columns=", "dataSource=", "rows.", ".map(")
    )
    return {
        "uses_WorkbenchPage": "<WorkbenchPage" in source_code,
        "uses_FilterToolbar": "FilterToolbar" in source_code,
        "uses_ListTable": "ListTable" in source_code,
        "uses_Drawer": "<Drawer" in source_code,
        "uses_design_tokens": any(
            t in all_sources for t in ("bg-card", "text-foreground", "border-border")
        ),
        "uses_animate_in": "animate-in" in all_sources,
        "uses_lucide_react": "lucide-react" in all_sources,
        "has_loading_state": any(
            t in all_sources for t in ("isLoading", "loading", "Skeleton", "LoadingRows")
        ),
        "has_empty_state": any(
            t in all_sources for t in ("empty", "暂无", "no data", "length === 0")
        ),
        "has_error_state": any(t in all_sources for t in ("error", "Error", "错误")),
        "has_tabular_data_intent": has_tabular_data_intent,
    }


def _derive_child_observations(child_component_texts: dict[str, Any]) -> list[str]:
    """Generate observations about features found in child components."""
    observations: list[str] = []
    for name, info in child_component_texts.items():
        excerpt = str(info.get("source_excerpt") or "")
        if "tenant_config" in excerpt or "configChecks" in excerpt:
            observations.append(f"子组件 {name} 包含租户配置检测(tenant_config_checks)渲染逻辑")
        if "overview" in name.lower() or "card" in name.lower():
            observations.append(f"子组件 {name} 负责概览卡片展示")
    return observations


def _truncate(value: str, *, max_chars: int = _MAX_EXCERPT_CHARS) -> str:
    normalized = str(value or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _looks_like_code_fragment(value: str) -> bool:
    text = _normalize_text(value)
    if not text:
        return False
    lowered = text.lower()
    code_markers = ("const ", "let ", "return ", "usestate", "=>", "(", ")", "[", "]", "{", "}")
    marker_hits = sum(1 for marker in code_markers if marker in lowered)
    if marker_hits >= 4:
        return True
    if re.search(r"\b[A-Za-z_]\w*\s*=\s*[^ ]", text):
        return True
    if re.search(r"[{}[\];]", text):
        return True
    return False


def _extract_named_const_blocks(source_code: str, *, names: tuple[str, ...]) -> dict[str, str]:
    lines = source_code.splitlines()
    blocks: dict[str, str] = {}
    for name in names:
        block = _extract_const_block(lines, name=name)
        if block:
            blocks[name] = _truncate(block, max_chars=700)
    return blocks


def _extract_const_block(lines: list[str], *, name: str) -> str:
    if not lines:
        return ""
    start = -1
    marker = re.compile(rf"^\s*const\s+{re.escape(name)}\s*=")
    for index, line in enumerate(lines):
        if marker.search(line):
            start = index
            break
    if start < 0:
        return ""

    block: list[str] = []
    next_const = re.compile(r"^\s*const\s+[A-Za-z_]\w*\s*=")
    for index in range(start, len(lines)):
        line = lines[index]
        if index > start and next_const.search(line):
            break
        if index > start and not line.strip():
            break
        block.append(line.rstrip())
        if len(block) >= 20:
            break
    return "\n".join(block).strip()


def _extract_function_block(lines: list[str], *, name: str) -> str:
    if not lines:
        return ""
    start = -1
    marker = re.compile(rf"^\s*function\s+{re.escape(name)}\s*\(")
    for index, line in enumerate(lines):
        if marker.search(line):
            start = index
            break
    if start < 0:
        return ""

    block: list[str] = []
    brace_balance = 0
    for index in range(start, len(lines)):
        line = lines[index]
        block.append(line.rstrip())
        brace_balance += line.count("{") - line.count("}")
        if index > start and brace_balance <= 0:
            break
        if len(block) >= 220:
            break
    return "\n".join(block).strip()


def _build_repo_review_scope_source(source_code: str) -> str:
    if "<Drawer" not in source_code:
        return source_code

    lines = source_code.splitlines()
    snippets: list[str] = []

    # 1. Page structure: imports + top of main component (first ~80 lines capture
    #    imports, type definitions, state declarations, and component hints)
    page_head = "\n".join(lines[:80]).strip()
    if page_head:
        snippets.append(page_head)

    # 2. Primary render area: the JSX return with WorkbenchPage / toolbar / primary
    primary_windows = _extract_source_windows(
        source_code,
        markers=("<WorkbenchPage", "const toolbar", "const primary", "return ("),
        max_chars=1800,
    )
    if primary_windows:
        snippets.append(primary_windows)

    # 3. Drawer-specific context
    const_blocks = _extract_named_const_blocks(
        source_code, names=("drawerTitle", "drawerDescription", "drawerContextText")
    )
    for key in ("drawerTitle", "drawerDescription", "drawerContextText"):
        block = const_blocks.get(key)
        if block:
            snippets.append(block)
    for fn_name in (
        "renderDiagnosisPanel",
        "renderDeepCheckPanel",
        "renderTenantConfigPanel",
        "StatsDrawerHeader",
        "StatsDrawerStatusStrip",
    ):
        block = _extract_function_block(lines, name=fn_name)
        if block:
            snippets.append(_truncate(block, max_chars=1200))
    drawer_windows = _extract_source_windows(
        source_code,
        markers=(
            "<Drawer open=",
            "<DrawerBody",
            'drawerMode === "chat"',
            'drawerMode === "detail"',
        ),
        max_chars=_MAX_DRAWER_CONTEXT_CHARS,
    )
    if drawer_windows:
        snippets.append(drawer_windows)
    merged = "\n\n".join(item for item in snippets if item)
    return merged if merged else source_code


_MAX_CHILD_EXCERPT_CHARS = 2400
_CHILD_IMPORT_PATTERN = re.compile(
    r'import\s+\{[^}]+\}\s+from\s+["\']@/components/(?!(?:ui|shared)/)([\w/-]+)["\']',
)


def _extract_child_component_texts(
    page_source: str,
    page_path: Path,
    repo_root: str | Path,
) -> dict[str, Any]:
    """Extract visible texts and source excerpts from domain-specific child components."""
    if not repo_root:
        return {}
    frontend_src = Path(repo_root) / "frontend" / "src"
    results: dict[str, Any] = {}
    for match in _CHILD_IMPORT_PATTERN.finditer(page_source):
        rel_module = match.group(1)
        candidate = frontend_src / "components" / f"{rel_module}.tsx"
        if not candidate.is_file():
            candidate = frontend_src / "components" / f"{rel_module}.ts"
        if not candidate.is_file():
            continue
        try:
            child_source = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        component_name = rel_module.rsplit("/", 1)[-1]
        results[component_name] = {
            "visible_texts": _extract_visible_texts_from_source(child_source)[:_MAX_VISIBLE_TEXTS],
            "component_hints": _extract_component_hints(child_source),
            "source_excerpt": _truncate(child_source, max_chars=_MAX_CHILD_EXCERPT_CHARS),
        }
    return results


def _extract_source_windows(source_code: str, *, markers: tuple[str, ...], max_chars: int) -> str:
    lines = source_code.splitlines()
    windows: list[str] = []
    for marker in markers:
        index = _find_first_line_index(lines, marker=marker)
        if index < 0:
            continue
        start = max(0, index - 4)
        end = min(len(lines), index + 14)
        window_lines = [_sanitize_review_window_line(line.rstrip()) for line in lines[start:end]]
        snippet = "\n".join(line for line in window_lines if line).strip()
        if snippet:
            windows.append(f"[marker:{marker}]\n{snippet}")
    return _truncate("\n\n".join(windows), max_chars=max_chars)


def _find_first_line_index(lines: list[str], *, marker: str) -> int:
    for index, line in enumerate(lines):
        if marker in line:
            return index
    return -1


def _sanitize_review_window_line(line: str) -> str:
    lowered = line.lower()
    if any(
        marker in lowered
        for marker in (
            "adapter={",
            "datasourceid={",
            "focusobject={",
            "suggestedprompt={",
            "onsuggestedpromptapplied={",
            "buildcontext",
        )
    ):
        return ""
    return line
