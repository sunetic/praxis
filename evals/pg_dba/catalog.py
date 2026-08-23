"""Versioned case catalog loading for the PostgreSQL DBA eval."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUITE_DIR = Path(__file__).resolve().parent
DEFAULT_CATALOG_PATH = SUITE_DIR / "cases.json"


@dataclass(frozen=True)
class AnswerCheck:
    """One weighted, deterministic answer requirement."""

    check_id: str
    description: str
    patterns: tuple[str, ...]
    weight: int


@dataclass(frozen=True)
class EvalCase:
    """A single versioned eval case and its deterministic rubric."""

    case_id: str
    title: str
    prompt: str
    answer_checks: tuple[AnswerCheck, ...]
    minimum_sql_calls: int
    knowledge_required: bool = False


@dataclass(frozen=True)
class EvalCatalog:
    """The complete suite definition."""

    suite: str
    version: str
    cases: tuple[EvalCase, ...]

    def by_id(self) -> dict[str, EvalCase]:
        return {case.case_id: case for case in self.cases}


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> EvalCatalog:
    """Load and validate a case catalog from JSON."""
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Eval catalog must define at least one case")

    cases: list[EvalCase] = []
    seen: set[str] = set()
    for item in raw_cases:
        if not isinstance(item, dict):
            raise ValueError("Each eval case must be an object")
        case_id = str(item.get("id") or "").strip()
        if not case_id or case_id in seen:
            raise ValueError(f"Invalid or duplicate eval case id: {case_id!r}")
        seen.add(case_id)
        checks: list[AnswerCheck] = []
        for raw_check in item.get("answer_checks") or []:
            patterns = tuple(str(value) for value in raw_check.get("patterns") or [])
            weight = int(raw_check.get("weight") or 0)
            if not patterns or weight <= 0:
                raise ValueError(f"{case_id} has an invalid answer check")
            checks.append(
                AnswerCheck(
                    check_id=str(raw_check["id"]),
                    description=str(raw_check["description"]),
                    patterns=patterns,
                    weight=weight,
                )
            )
        if not checks:
            raise ValueError(f"{case_id} must define answer checks")
        cases.append(
            EvalCase(
                case_id=case_id,
                title=str(item["title"]),
                prompt=str(item["prompt"]),
                answer_checks=tuple(checks),
                minimum_sql_calls=max(0, int(item.get("minimum_sql_calls") or 0)),
                knowledge_required=bool(item.get("knowledge_required", False)),
            )
        )
    return EvalCatalog(
        suite=str(payload.get("suite") or "pg-dba"),
        version=str(payload.get("version") or "unversioned"),
        cases=tuple(cases),
    )
