from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PageTemplateQualityReport:
    ok: bool
    score: int
    issues: list[str]
    warnings: list[str]
    metrics: dict[str, int | bool]


def _contains(text: str, marker: str) -> bool:
    return marker in text


def _count(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def analyze_page_preview_quality(html: str) -> PageTemplateQualityReport:
    normalized = str(html or "")
    has_filter_row = _contains(normalized, "filter-row")
    has_chart_grid = _contains(normalized, "chart-grid")
    has_table = _contains(normalized.lower(), "<table")
    has_pagination = _contains(normalized, "pagination")
    has_drawer = _contains(normalized, "drawer")
    has_line_chart = ("LineChart" in normalized) or ("series-line" in normalized)
    has_bar_chart = ("BarChart" in normalized) or ("series-bar" in normalized)
    h1_count = _count(normalized, r"<h1\b")
    card_count = normalized.count("class='card'") + normalized.count('class="card"')

    region_count = sum(
        [
            bool(has_filter_row),
            bool(has_chart_grid),
            bool(has_table),
            bool(has_pagination),
            bool(has_drawer),
        ]
    )
    issues: list[str] = []
    if not has_filter_row:
        issues.append("missing_filter_region")
    if not has_chart_grid:
        issues.append("missing_chart_region")
    if not has_table:
        issues.append("missing_table_region")
    if has_table and not has_pagination:
        issues.append("table_without_pagination")
    if not has_drawer:
        issues.append("missing_detail_drawer")
    if not (has_line_chart and has_bar_chart):
        issues.append("chart_variety_low")
    if region_count < 4:
        issues.append("layout_diversity_low")
    if h1_count > 1:
        issues.append("title_hierarchy_violated")
    if card_count < 3:
        issues.append("insufficient_information_density")

    # Design compliance warnings (do not affect ok/score)
    warnings: list[str] = []
    body_only = re.sub(r"<style[^>]*>.*?</style>", "", normalized, flags=re.DOTALL | re.IGNORECASE)
    body_only = re.sub(r"(?:stop-color|var\()[^)]*\)", "", body_only)
    hardcoded_hex_count = len(re.findall(r"""(?<!['"-])#[0-9a-fA-F]{3,8}\b""", body_only))
    if hardcoded_hex_count > 0:
        warnings.append("hardcoded_color")
    has_entry_animation = (
        "animate-in" in normalized or "transition" in normalized or "animation" in normalized
    )
    if not has_entry_animation:
        warnings.append("missing_entry_animation")
    if h1_count >= 1:
        warnings.append("page_level_h1")

    score = max(0, 100 - len(set(issues)) * 14)
    return PageTemplateQualityReport(
        ok=not issues,
        score=score,
        issues=sorted(set(issues)),
        warnings=sorted(set(warnings)),
        metrics={
            "has_filter_row": has_filter_row,
            "has_chart_grid": has_chart_grid,
            "has_table": has_table,
            "has_pagination": has_pagination,
            "has_drawer": has_drawer,
            "has_line_chart": has_line_chart,
            "has_bar_chart": has_bar_chart,
            "region_count": region_count,
            "card_count": card_count,
            "h1_count": h1_count,
            "hardcoded_hex_count": hardcoded_hex_count,
            "has_entry_animation": has_entry_animation,
        },
    )
