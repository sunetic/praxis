from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

ReviewVerdict = Literal["pass", "warning", "fail"]


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    category: str
    summary: str
    evidence: str
    why_it_conflicts_with_purpose: str
    suggested_fix: str

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewResult:
    verdict: ReviewVerdict
    summary: str
    findings: list[ReviewFinding]

    def to_payload(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "summary": self.summary,
            "findings": [item.to_payload() for item in self.findings],
        }
