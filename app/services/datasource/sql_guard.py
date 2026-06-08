import hashlib
import json
import re
import uuid

from app.models import models

READ_ONLY_FIRST_KEYWORDS = {
    "select",
    "show",
    "desc",
    "describe",
    "explain",
}
SAFE_SESSION_KEYWORDS = {
    "use",
}
MUTATING_KEYWORDS = {
    "insert",
    "update",
    "delete",
    "replace",
    "merge",
    "call",
    "alter",
    "create",
    "drop",
    "truncate",
    "rename",
    "grant",
    "revoke",
    "set",
    "analyze",
    "optimize",
    "flush",
    "repair",
    "lock",
    "unlock",
    "start",
    "commit",
    "rollback",
    "savepoint",
    "release",
}


def _strip_sql_comments(sql: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    no_line = re.sub(r"--[^\n]*", " ", no_block)
    return no_line.strip()


def _first_keyword(sql: str) -> str:
    cleaned = _strip_sql_comments(sql).lower()
    if not cleaned:
        return ""
    match = re.match(r"([a-z_]+)", cleaned)
    return match.group(1) if match else ""


def is_mutating_sql(sql: str) -> bool:
    keyword = _first_keyword(sql)
    if not keyword:
        return False
    if keyword in READ_ONLY_FIRST_KEYWORDS:
        return False
    if keyword in SAFE_SESSION_KEYWORDS:
        return False
    if keyword == "with":
        lowered = _strip_sql_comments(sql).lower()
        for token in MUTATING_KEYWORDS:
            if re.search(rf"\b{re.escape(token)}\b", lowered):
                return True
        return False
    return True


def normalize_sql(sql: str) -> str:
    compact = re.sub(r"\s+", " ", _strip_sql_comments(sql))
    return compact.strip()


def build_execution_fingerprint(
    *,
    sql: str,
    resolved_datasource_id: int,
    resolved_role: str,
    tenant_fingerprint: dict[str, str],
) -> str:
    payload = {
        "sql": normalize_sql(sql),
        "resolved_datasource_id": resolved_datasource_id,
        "resolved_role": resolved_role,
        "tenant_fingerprint": tenant_fingerprint,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_action_token() -> str:
    return uuid.uuid4().hex


def redact_sql_preview(sql: str, max_length: int = 320) -> str:
    normalized = normalize_sql(sql)
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[:max_length]}..."


def compare_tenant_fingerprint(expected: dict[str, str], current: dict[str, str]) -> list[str]:
    mismatches: list[str] = []
    for key, value in expected.items():
        current_value = current.get(key)
        if current_value != value:
            mismatches.append(f"{key}: expected={value}, actual={current_value}")
    return mismatches


async def probe_tenant_fingerprint(pool, datasource: models.DataSource, role: str) -> dict[str, str]:
    probes = [
        ("effective_tenant_id", "SELECT effective_tenant_id() AS v"),
        ("current_user", "SELECT CURRENT_USER() AS v"),
        ("database_name", "SELECT DATABASE() AS v"),
    ]
    fingerprint: dict[str, str] = {}

    for key, sql in probes:
        try:
            result = await pool.execute_query(datasource, sql, role=role)
            rows = result.get("rows") or []
            if not rows:
                continue
            value = rows[0].get("v")
            if value is None:
                continue
            fingerprint[key] = str(value)
        except Exception:
            continue

    if not fingerprint:
        # Last-resort metadata fallback. This is weaker than runtime probes but keeps
        # action cards informative in restricted environments.
        if datasource.user:
            fingerprint["metadata_user"] = str(datasource.user)
        if datasource.database:
            fingerprint["metadata_database"] = str(datasource.database)
    return fingerprint

