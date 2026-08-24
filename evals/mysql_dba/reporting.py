"""MySQL DBA report compatibility wrapper."""

from pathlib import Path
from typing import Any

from evals.dba.reporting import render_markdown as _render_markdown
from evals.dba.reporting import write_reports as _write_reports

REPORT_TITLE = "Praxis MySQL DBA Eval Report"


def render_markdown(summary: dict[str, Any], baseline: dict[str, Any] | None = None) -> str:
    """Render a MySQL DBA scorecard."""
    return _render_markdown(summary, baseline, title=REPORT_TITLE)


def write_reports(
    output_dir: Path,
    summary: dict[str, Any],
    baseline: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """Write MySQL DBA JSON and Markdown reports."""
    return _write_reports(output_dir, summary, baseline, title=REPORT_TITLE)


__all__ = ["render_markdown", "write_reports"]
