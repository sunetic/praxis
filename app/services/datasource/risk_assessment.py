"""Conservative, structured preflight assessment for database changes.

The model supplies operational evidence, but it cannot lower the server's syntax-based risk floor.
The resulting assessment is bound into the durable call fingerprint and shown with the approval.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp, parse

RiskLevel = Literal["low", "medium", "high", "critical"]
_RISK_RANK: dict[RiskLevel, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class DatabaseChangeRisk(BaseModel):
    """Evidence and recovery information supplied before requesting a write."""

    model_config = ConfigDict(extra="forbid")

    risk_level: RiskLevel = Field(
        description="Risk level supported by the observed evidence; uncertainty raises, never lowers it."
    )
    summary: str = Field(min_length=1, max_length=2000)
    observed_facts: list[str] = Field(
        min_length=1,
        max_length=20,
        description="Current facts obtained from read-only checks, not assumptions or planned checks.",
    )
    estimated_affected_rows: int | None = Field(default=None, ge=0)
    estimated_table_rows: int | None = Field(default=None, ge=0)
    row_estimate_basis: str = Field(
        min_length=1,
        max_length=1000,
        description="How row estimates were obtained, or why row counts do not apply.",
    )
    lock_and_availability_impact: str = Field(min_length=1, max_length=2000)
    resource_and_replication_impact: str = Field(min_length=1, max_length=2000)
    rollback_plan: str = Field(min_length=1, max_length=2000)
    verification_plan: str = Field(min_length=1, max_length=2000)


def _raise_level(current: RiskLevel, candidate: RiskLevel) -> RiskLevel:
    return candidate if _RISK_RANK[candidate] > _RISK_RANK[current] else current


def assess_database_change(sql: str, dialect: str, supplied: DatabaseChangeRisk) -> dict:
    """Return the reviewable assessment, enforcing a server-owned risk floor."""
    statement = parse(sql, read=dialect)[0]
    floor: RiskLevel = "low"
    reasons: list[str] = []
    needs_affected_rows = False
    needs_table_rows = False

    if isinstance(statement, (exp.Update, exp.Delete)):
        needs_affected_rows = True
        if statement.args.get("where") is None:
            floor = "critical"
            reasons.append("UPDATE/DELETE has no WHERE predicate")
        else:
            floor = "medium"
            reasons.append("UPDATE/DELETE can lock or modify multiple matching rows")
    elif isinstance(statement, exp.Insert):
        needs_affected_rows = True
        values = statement.args.get("expression")
        literal_rows = len(values.expressions) if isinstance(values, exp.Values) else None
        if literal_rows is None:
            floor = "medium"
            reasons.append("INSERT source cardinality is not fixed by a literal VALUES list")
        elif supplied.estimated_affected_rows is not None:
            if supplied.estimated_affected_rows < literal_rows:
                raise ValueError(
                    "estimated_affected_rows cannot be lower than the literal INSERT row count."
                )
            reasons.append(
                f"literal INSERT contains {literal_rows} row{'s' if literal_rows != 1 else ''}"
            )
        else:
            reasons.append("INSERT changes persistent data and may affect indexes or replication")
    elif isinstance(statement, exp.Create):
        kind = str(statement.args.get("kind") or "").upper()
        if kind == "INDEX":
            floor = "high"
            needs_table_rows = True
            reasons.append(
                "index creation can scan the table, consume I/O, and block concurrent work"
            )
        else:
            floor = "medium"
            reasons.append("DDL can acquire metadata locks and affect dependent sessions")
    elif isinstance(statement, exp.Alter):
        floor = "high"
        needs_table_rows = True
        reasons.append("ALTER may take strong locks or rewrite the target object")
    elif isinstance(statement, exp.TruncateTable):
        floor = "critical"
        needs_table_rows = True
        reasons.append("TRUNCATE is destructive and commonly takes an exclusive lock")
    elif isinstance(statement, exp.Drop):
        floor = "critical"
        needs_table_rows = str(statement.args.get("kind") or "").upper() == "TABLE"
        reasons.append("DROP is destructive and may break dependent objects or applications")
    elif isinstance(statement, (exp.Grant, exp.Revoke)):
        floor = "high"
        reasons.append("privilege changes alter the security boundary")
    else:
        floor = "high"
        reasons.append("the change type has no lower-risk server policy")

    if needs_affected_rows and supplied.estimated_affected_rows is None:
        raise ValueError("Risk assessment requires estimated_affected_rows for this data change.")
    if needs_table_rows and supplied.estimated_table_rows is None:
        raise ValueError("Risk assessment requires estimated_table_rows for this schema change.")

    scale = max(
        supplied.estimated_affected_rows or 0,
        supplied.estimated_table_rows or 0,
    )
    if scale >= 1_000_000:
        floor = _raise_level(floor, "critical")
        reasons.append("the supplied row estimate is at least 1,000,000 rows")
    elif scale >= 100_000:
        floor = _raise_level(floor, "high")
        reasons.append("the supplied row estimate is at least 100,000 rows")
    elif scale >= 10_000:
        floor = _raise_level(floor, "medium")
        reasons.append("the supplied row estimate is at least 10,000 rows")

    enforced = _raise_level(supplied.risk_level, floor)
    assessment = supplied.model_dump(mode="json")
    assessment.update(
        {
            "reported_risk_level": supplied.risk_level,
            "risk_level": enforced,
            "server_risk_floor": floor,
            "server_risk_reasons": reasons,
            "requires_explicit_approval": enforced != "low",
            "auto_approval_eligible": enforced == "low",
        }
    )
    return assessment
