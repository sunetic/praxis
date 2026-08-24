"""Versioned DBA eval case catalog loading."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AnswerCheck:
    """One weighted reference-answer requirement."""

    check_id: str
    description: str
    patterns: tuple[str, ...]
    weight: int
    dimension: str = "outcome"


@dataclass(frozen=True)
class EvidenceRequirement:
    """A source-neutral requirement for supporting authoritative evidence."""

    requirement_id: str
    description: str
    claim_check_ids: tuple[str, ...]
    citation_patterns: tuple[str, ...]


@dataclass(frozen=True)
class EvalCase:
    """A task plus reference answer and source-neutral grading rubric."""

    case_id: str
    title: str
    prompt: str
    answer_checks: tuple[AnswerCheck, ...]
    evidence_requirements: tuple[EvidenceRequirement, ...] = ()
    outcome_threshold: int = 60
    quality_threshold: int = 60


@dataclass(frozen=True)
class EvalCatalog:
    """The complete suite definition."""

    suite: str
    version: str
    cases: tuple[EvalCase, ...]

    def by_id(self) -> dict[str, EvalCase]:
        """Return cases keyed by their stable IDs."""
        return {case.case_id: case for case in self.cases}


def load_catalog(path: Path) -> EvalCatalog:
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
            dimension = str(raw_check.get("dimension") or "outcome")
            if dimension not in {"outcome", "quality"}:
                raise ValueError(f"{case_id} has an invalid check dimension: {dimension!r}")
            checks.append(
                AnswerCheck(
                    check_id=str(raw_check["id"]),
                    description=str(raw_check["description"]),
                    patterns=patterns,
                    weight=weight,
                    dimension=dimension,
                )
            )
        if not checks:
            raise ValueError(f"{case_id} must define answer checks")
        evidence_requirements: list[EvidenceRequirement] = []
        known_check_ids = {check.check_id for check in checks}
        for raw_requirement in item.get("evidence_requirements") or []:
            claim_check_ids = tuple(
                str(value) for value in raw_requirement.get("claim_check_ids") or []
            )
            citation_patterns = tuple(
                str(value) for value in raw_requirement.get("citation_patterns") or []
            )
            if not claim_check_ids or not citation_patterns:
                raise ValueError(f"{case_id} has an invalid evidence requirement")
            unknown = set(claim_check_ids) - known_check_ids
            if unknown:
                raise ValueError(
                    f"{case_id} evidence requirement references unknown checks: {sorted(unknown)}"
                )
            evidence_requirements.append(
                EvidenceRequirement(
                    requirement_id=str(raw_requirement["id"]),
                    description=str(raw_requirement["description"]),
                    claim_check_ids=claim_check_ids,
                    citation_patterns=citation_patterns,
                )
            )
        thresholds = item.get("thresholds") or {}
        outcome_threshold = int(thresholds.get("outcome") or 60)
        quality_threshold = int(thresholds.get("quality") or 60)
        if not 0 <= outcome_threshold <= 100 or not 0 <= quality_threshold <= 100:
            raise ValueError(f"{case_id} thresholds must be between 0 and 100")
        cases.append(
            EvalCase(
                case_id=case_id,
                title=str(item["title"]),
                prompt=str(item["prompt"]),
                answer_checks=tuple(checks),
                evidence_requirements=tuple(evidence_requirements),
                outcome_threshold=outcome_threshold,
                quality_threshold=quality_threshold,
            )
        )
    return EvalCatalog(
        suite=str(payload.get("suite") or "dba"),
        version=str(payload.get("version") or "unversioned"),
        cases=tuple(cases),
    )
