from __future__ import annotations

import re
from dataclasses import dataclass

_QUOTED_RE = re.compile(r"[\"'“”‘’`]([^\"'“”‘’`]{2,200})[\"'“”‘’`]")
_IDENTIFIER_RE = re.compile(
    r"\b(?:"
    r"SQLSTATE\s*\[?[0-9A-Z]{5}\]?|"
    r"ORA-\d{3,6}|"
    r"[A-Z]{2,}(?:_[A-Z0-9]+)+|"
    r"[A-Za-z]+[-_.][A-Za-z0-9_.-]*\d[A-Za-z0-9_.-]*|"
    r"\d{4,6}"
    r")\b",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{1,80}|[\u4e00-\u9fff]{2,24}")


_CONCEPT_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        (
            "错误",
            "报错",
            "失败",
            "异常",
            "error",
            "failed",
            "failure",
            "fatal",
            "critical",
            "exception",
        ),
        ("错误", "报错", "error", "errors", "failed", "failure", "fatal", "critical", "exception"),
    ),
    (
        ("死锁", "锁等待", "锁冲突", "deadlock", "lock wait", "blocking"),
        ("死锁", "锁等待", "锁冲突", "deadlock", "dead lock", "lock wait", "blocked", "blocking"),
    ),
    (
        ("慢查询", "慢 sql", "slow query", "latency", "超时", "timeout"),
        ("慢查询", "慢 SQL", "slow query", "latency", "duration", "timeout", "performance"),
    ),
    (
        ("复制", "主从", "replication", "replica", "standby", "延迟", "lag"),
        ("复制", "主从", "replication", "replica", "primary", "source", "standby", "lag"),
    ),
    (
        ("连接", "会话", "connection", "connect", "session"),
        ("连接", "会话", "connection", "connect", "session", "client", "disconnect"),
    ),
    (
        ("索引", "index", "btree", "b-tree"),
        ("索引", "index", "indexes", "indexing", "B-tree", "btree"),
    ),
)


def _dedupe(values: list[str], *, limit: int = 32) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _format_variants(identifier: str) -> list[str]:
    variants = [identifier]
    if "_" in identifier:
        variants.extend([identifier.replace("_", " "), identifier.replace("_", "-")])
    if "-" in identifier and not identifier.upper().startswith("ORA-"):
        variants.extend([identifier.replace("-", " "), identifier.replace("-", "_")])
    return variants


@dataclass(frozen=True)
class QueryPlan:
    original_query: str
    groups: dict[str, tuple[str, ...]]
    discovery_terms: tuple[str, ...]

    @property
    def all_patterns(self) -> tuple[str, ...]:
        values: list[str] = []
        for patterns in self.groups.values():
            values.extend(patterns)
        return _dedupe(values, limit=80)

    @property
    def required_groups(self) -> tuple[str, ...]:
        return tuple(name for name, patterns in self.groups.items() if patterns)

    def to_prompt_dict(self) -> dict[str, list[str] | str]:
        return {
            "original_query": self.original_query,
            **{name: list(patterns) for name, patterns in self.groups.items()},
            "discovery_terms": list(self.discovery_terms),
        }


def build_query_plan(query: str) -> QueryPlan:
    text = str(query or "").strip()
    lowered = text.casefold()

    exact_values: list[str] = []
    for match in _QUOTED_RE.finditer(text):
        exact_values.append(re.escape(match.group(1).strip()))

    identifiers: list[str] = []
    for match in _IDENTIFIER_RE.finditer(text):
        identifiers.extend(_format_variants(match.group(0).strip()))

    original_terms: list[str] = []
    for token in _TOKEN_RE.findall(text):
        if len(token) > 1:
            original_terms.append(re.escape(token))

    semantic_values: list[str] = []
    for triggers, variants in _CONCEPT_GROUPS:
        if any(trigger.casefold() in lowered for trigger in triggers):
            semantic_values.extend(re.escape(value) for value in variants)

    groups = {
        "exact_phrases": _dedupe(exact_values, limit=12),
        "identifiers": _dedupe(identifiers, limit=16),
        "original_terms": _dedupe(original_terms, limit=16),
        "semantic_variants": _dedupe(semantic_values, limit=24),
    }

    discovery_values: list[str] = []
    discovery_values.extend(
        value for value in _TOKEN_RE.findall(text) if 1 < len(value) <= 40 and not value.isdigit()
    )
    for pattern in groups["semantic_variants"]:
        if re.fullmatch(r"[\w\u4e00-\u9fff -]{2,40}", pattern):
            discovery_values.append(pattern)

    return QueryPlan(
        original_query=text,
        groups=groups,
        discovery_terms=_dedupe(discovery_values, limit=24),
    )
