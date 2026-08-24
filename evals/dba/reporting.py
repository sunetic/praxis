"""Human-readable and machine-readable DBA eval reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _delta(current: int | float, previous: int | float | None) -> str:
    if previous is None:
        return "—"
    value = current - previous
    return f"{value:+.1f}" if isinstance(value, float) else f"{value:+d}"


def render_markdown(
    summary: dict[str, Any],
    baseline: dict[str, Any] | None = None,
    *,
    title: str,
) -> str:
    """Render an eval summary as a compact Markdown scorecard."""
    aggregate = summary["aggregate"]
    previous = baseline.get("aggregate", {}) if baseline else {}
    run_config = summary.get("run_config") or {}
    safety_label = (
        "N/A"
        if int(aggregate.get("attempts") or 0) == 0
        else "PASS"
        if aggregate["safety_passed"]
        else "FAIL"
    )
    lines = [
        f"# {title}",
        "",
        f"- Run: `{summary['run_id']}`",
        f"- Commit: `{summary['commit']}`",
        f"- Working tree: `{'unknown' if summary.get('working_tree_dirty') is None else 'dirty' if summary.get('working_tree_dirty') else 'clean'}`",
        f"- Suite: `{summary['suite']}@{summary['suite_version']}`",
        f"- Profile: `{summary.get('profile', 'praxis')}`",
        f"- Model: `{summary['model']}`",
        f"- Started: `{summary['started_at']}`",
    ]
    if run_config:
        lines.extend(
            [
                f"- Case selection: `{run_config.get('case', 'all')}`",
                f"- Repeat: `{run_config.get('repeat', 1)}`",
                f"- Case timeout: `{run_config.get('case_timeout_seconds', 'unknown')}s`",
                f"- Case delay: `{run_config.get('case_delay_seconds', 'unknown')}s`",
                f"- Workload repeats: `{run_config.get('workload_repeats', 'unknown')}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Scorecard",
            "",
            "| Metric | Current | Baseline delta |",
            "| --- | ---: | ---: |",
            f"| Pass rate | {aggregate['pass_rate'] * 100:.1f}% | {_delta(aggregate['pass_rate'] * 100, previous.get('pass_rate', None) * 100 if previous.get('pass_rate') is not None else None)} |",
            f"| Reliable case rate | {aggregate.get('reliable_case_rate', 0) * 100:.1f}% | {_delta(aggregate.get('reliable_case_rate', 0) * 100, previous.get('reliable_case_rate', None) * 100 if previous.get('reliable_case_rate') is not None else None)} |",
            f"| Reliability | {aggregate['reliability_score']}/100 | {_delta(aggregate['reliability_score'], previous.get('reliability_score'))} |",
            f"| Task outcome | {aggregate['outcome_score']}/100 | {_delta(aggregate['outcome_score'], previous.get('outcome_score'))} |",
            f"| Answer quality | {aggregate['answer_quality_score']}/100 | {_delta(aggregate['answer_quality_score'], previous.get('answer_quality_score'))} |",
            f"| Required evidence | {aggregate['evidence_score']}/100 | {_delta(aggregate['evidence_score'], previous.get('evidence_score'))} |",
            f"| Safety | {safety_label} | — |",
            f"| Provider availability | {summary['provider_availability'] * 100:.1f}% | — |",
            f"| Avg task latency | {aggregate.get('average_duration_seconds', 0):.1f}s | {_delta(aggregate.get('average_duration_seconds', 0.0), previous.get('average_duration_seconds'))} |",
            f"| Avg input tokens | {aggregate.get('average_input_tokens', 0):,} | {_delta(aggregate.get('average_input_tokens', 0), previous.get('average_input_tokens'))} |",
            f"| Avg output tokens | {aggregate.get('average_output_tokens', 0):,} | {_delta(aggregate.get('average_output_tokens', 0), previous.get('average_output_tokens'))} |",
            f"| Avg LLM calls | {aggregate.get('average_llm_calls', 0)} | {_delta(aggregate.get('average_llm_calls', 0), previous.get('average_llm_calls'))} |",
            f"| Avg tool calls | {aggregate.get('average_tool_calls', 0)} | {_delta(aggregate.get('average_tool_calls', 0), previous.get('average_tool_calls'))} |",
            f"| Avg failed tool calls | {aggregate.get('average_failed_tool_calls', 0)} | {_delta(aggregate.get('average_failed_tool_calls', 0), previous.get('average_failed_tool_calls'))} |",
            f"| Avg verifier attempts | {aggregate.get('average_verification_attempts', 0)} | {_delta(aggregate.get('average_verification_attempts', 0), previous.get('average_verification_attempts'))} |",
            "",
            "## Cases",
            "",
            "| Case | Attempt | Status | Outcome | Quality | Evidence | Safety | Tools | Failed | Verify | LLM | Tokens | Seconds |",
            "| --- | ---: | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in summary["results"]:
        score = item["score"]
        metrics = item.get("runtime_metrics") or {}
        diagnostics = score.get("diagnostics") or {}
        tokens = int(metrics.get("input_tokens") or 0) + int(metrics.get("output_tokens") or 0)
        lines.append(
            f"| {item['case_id']} | {item['attempt']} | {score['status']} | "
            f"{score['outcome_score']} | {score['answer_quality_score']} | "
            f"{score['evidence_score']} | {'PASS' if score['safety_passed'] else 'FAIL'} | "
            f"{int(diagnostics.get('tool_calls') or 0)} | "
            f"{int(diagnostics.get('failed_tool_calls') or 0)} | "
            f"{int(metrics.get('verification_attempts') or 0)} | "
            f"{int(metrics.get('llm_calls') or 0)} | {tokens:,} | {item['duration_seconds']:.1f} |"
        )
    startup_failed = bool(summary.get("startup_error"))
    runtime_failed = bool(summary.get("runtime_error"))
    if startup_failed or runtime_failed:
        public_failure = (
            "Provider availability check failed before the Eval started."
            if startup_failed
            else "The Eval runtime did not complete."
        )
        lines.extend(
            [
                "",
                "## Runtime failure",
                "",
                public_failure,
                "Detailed diagnostics remain in the local run artifacts and must be reviewed "
                "before sharing.",
            ]
        )
    failed = [item for item in summary["results"] if item["score"]["status"] != "passed"]
    if failed:
        lines.extend(["", "## Failures", ""])
        for item in failed:
            score = item["score"]
            lines.append(
                f"- **{item['case_id']} attempt {item['attempt']}** — "
                f"`{score['status']}`; missing outcome: "
                f"{', '.join(score['failed_outcome_checks']) or 'none'}; "
                f"quality: {', '.join(score['failed_quality_checks']) or 'none'}; "
                f"evidence: {', '.join(score['failed_evidence_checks']) or 'none'}; terminal: "
                f"`{score['terminal_status']}`."
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Task outcome is the primary correctness measure. Answer quality and source-neutral evidence requirements are reported separately. Safety is a hard gate based on successfully executed SQL plus before/after database invariants. Tool calls, retries, tokens, and latency are diagnostics rather than prescribed steps. The `praxis` profile evaluates the model and Praxis harness together; the `model` profile uses the fixed Eval harness for model comparison. Raw evidence is stored beside this report for manual audit.",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(
    output_dir: Path,
    summary: dict[str, Any],
    baseline: dict[str, Any] | None = None,
    *,
    title: str,
) -> tuple[Path, Path]:
    """Write JSON and Markdown reports and return their paths."""
    json_path = output_dir / "summary.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(summary, baseline, title=title), encoding="utf-8")
    return json_path, markdown_path
