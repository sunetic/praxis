from __future__ import annotations

from .contract import CapabilityContext, CapabilitySummary


_SECTION_TITLES = {
    "tools": "Available Platform Tools",
    "tool_hints": "Contextual Tool Hints",
    "datasource": "Current Datasource",
    "services": "Available Services",
    "knowledge": "Knowledge Resources",
    "skills": "Active Skills",
    "scene": "Scene Context",
    "scope": "Scope Context",
}


def _render_summary(item: CapabilitySummary) -> str:
    line = f"- {item.name} — {item.purpose}. {item.availability}."
    if item.status:
        line = f"{line[:-1]} status={item.status}."
    if item.hints:
        line += " " + " ".join(item.hints)
    return line


def render_capability_context(context: CapabilityContext) -> str:
    blocks: list[str] = []
    for section_key in ["tools", "tool_hints", "datasource", "services", "knowledge", "skills", "scene", "scope"]:
        items = context.sections.get(section_key) or []
        if not items:
            continue
        title = _SECTION_TITLES[section_key]
        lines = [f"\n\n{title}:"]
        lines.extend(_render_summary(item) for item in items)
        blocks.append("\n".join(lines))
    return "".join(blocks)
