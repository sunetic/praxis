from app.core.config import BUILTIN_SKILLS_DIR

PACK_META = {
    "id": "builtin-postgresql-diagnosis",
    "name": "PostgreSQL Diagnosis Runbooks",
    "description": (
        "Built-in PostgreSQL diagnostic runbooks covering connection health, "
        "locks, replication, vacuum health, and slow-query triage."
    ),
    "tags": ["postgresql", "diagnosis"],
    "db_type": "postgresql",
    "type": "local",
    "local_path": str(BUILTIN_SKILLS_DIR / "postgresql"),
    "repo_url": "",
    "branch": "",
    "subdirectory": "",
    "license": "bundled",
    "estimated_doc_count": 5,
    "estimated_size_mb": 0.1,
}
