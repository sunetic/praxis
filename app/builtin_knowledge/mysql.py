from app.core.config import DEFAULT_DATA_DIR

PACK_META = {
    "id": "builtin-mysql-diagnosis",
    "name": "MySQL Diagnosis Runbooks",
    "description": (
        "Built-in MySQL diagnostic runbooks covering connection health, "
        "InnoDB, locks, replication, and slow-query triage."
    ),
    "tags": ["mysql", "diagnosis"],
    "db_type": "mysql",
    "type": "local",
    "local_path": str(DEFAULT_DATA_DIR / "skills" / "mysql"),
    "repo_url": "",
    "branch": "",
    "subdirectory": "",
    "license": "bundled",
    "estimated_doc_count": 5,
    "estimated_size_mb": 0.1,
}
