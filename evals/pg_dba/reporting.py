"""Human-readable and machine-readable PG DBA eval reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _delta(current: int | float, previous: int | float | None) -> str:
    if previous is None:
        return "—"
    value = current - previous
    return f"{value:+.1f}" if isinstance(value, float) else f"{value:+d}"


def render_markdown(summary: dict[str, Any], baseline: dict[str, Any] | None = None) -> str:
    """Render an eval summary as a compact Markdown scorecard."""
    aggregate = summary["aggregate"]
    previous = baseline.get("aggregate", {}) if baseline else {}
    safety_label = (
        "N/A"
        if int(aggregate.get("attempts") or 0) == 0
        else "PASS"
        if aggregate["safety_passed"]
        else "FAIL"
    )
    lines = [
        "# Praxis PostgreSQL DBA Eval Report",
        "",
        f"- Run: `{summary['run_id']}`",
        f"- Commit: `{summary['commit']}`",
        f"- Suite: `{summary['suite']}@{summary['suite_version']}`",
        f"- Model: `{summary['model']}`",
        f"- Provider: `{summary['provider_host']}`",
        f"- Started: `{summary['started_at']}`",
        "",
        "## Scorecard",
        "",
        "| Metric | Current | Baseline delta |",
        "| --- | ---: | ---: |",
        f"| Pass rate | {aggregate['pass_rate'] * 100:.1f}% | {_delta(aggregate['pass_rate'] * 100, previous.get('pass_rate', None) * 100 if previous.get('pass_rate') is not None else None)} |",
        f"| Reliability | {aggregate['reliability_score']}/100 | {_delta(aggregate['reliability_score'], previous.get('reliability_score'))} |",
        f"| Grounded intelligence | {aggregate['intelligence_score']}/100 | {_delta(aggregate['intelligence_score'], previous.get('intelligence_score'))} |",
        f"| Safety | {safety_label} | — |",
        f"| Provider availability | {summary['provider_availability'] * 100:.1f}% | — |",
        f"| Avg task latency | {aggregate.get('average_duration_seconds', 0):.1f}s | {_delta(aggregate.get('average_duration_seconds', 0.0), previous.get('average_duration_seconds'))} |",
        f"| Avg input tokens | {aggregate.get('average_input_tokens', 0):,} | {_delta(aggregate.get('average_input_tokens', 0), previous.get('average_input_tokens'))} |",
        f"| Avg output tokens | {aggregate.get('average_output_tokens', 0):,} | {_delta(aggregate.get('average_output_tokens', 0), previous.get('average_output_tokens'))} |",
        f"| Avg LLM calls | {aggregate.get('average_llm_calls', 0)} | {_delta(aggregate.get('average_llm_calls', 0), previous.get('average_llm_calls'))} |",
        "",
        "## Cases",
        "",
        "| Case | Attempt | Status | Reliability | Intelligence | SQL | KB | LLM calls | Tokens | Seconds |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in summary["results"]:
        score = item["score"]
        metrics = item.get("runtime_metrics") or {}
        tokens = int(metrics.get("input_tokens") or 0) + int(metrics.get("output_tokens") or 0)
        lines.append(
            f"| {item['case_id']} | {item['attempt']} | {score['status']} | "
            f"{score['reliability_score']} | {score['intelligence_score']} | "
            f"{score['sql_calls']} | {score['knowledge_calls']} | "
            f"{int(metrics.get('llm_calls') or 0)} | {tokens:,} | {item['duration_seconds']:.1f} |"
        )
    runtime_error = summary.get("runtime_error") or summary.get("startup_error")
    if runtime_error:
        lines.extend(["", "## Runtime failure", "", f"`{runtime_error}`"])
    failed = [item for item in summary["results"] if item["score"]["status"] != "passed"]
    if failed:
        lines.extend(["", "## Failures", ""])
        for item in failed:
            score = item["score"]
            lines.append(
                f"- **{item['case_id']} attempt {item['attempt']}** — "
                f"`{score['status']}`; missing: "
                f"{', '.join(score['failed_checks']) or 'none'}; terminal: "
                f"`{score['terminal_status']}`."
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Reliability measures whether Praxis completes the real task path. Provider availability separately measures HTTP/stream connectivity, so an incomplete reasoning loop is not mislabeled as a supplier outage. Grounded intelligence measures versioned, case-specific evidence coverage; it is not a style score. Safety is a hard gate based on executed SQL plus before/after database invariants. Raw evidence is stored beside this report for manual audit.",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(
    output_dir: Path,
    summary: dict[str, Any],
    baseline: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """Write JSON and Markdown reports and return their paths."""
    json_path = output_dir / "summary.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(summary, baseline), encoding="utf-8")
    return json_path, markdown_path
