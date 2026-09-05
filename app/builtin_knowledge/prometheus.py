from pathlib import Path

PACK_META = {
    "id": "prometheus-http-api",
    "name": "Prometheus HTTP API",
    "description": (
        "Bundled operational reference for Prometheus instant and range queries, "
        "active alerts, and MySQL exporter correlation workflows."
    ),
    "tags": ["prometheus", "monitoring", "alerts", "mysql", "http-api"],
    "db_type": None,
    "type": "local",
    "local_path": str(Path(__file__).with_name("prometheus_api")),
    "repo_url": "",
    "branch": "",
    "subdirectory": "",
    "license": "Documentation summary with links to Apache-2.0 sources",
    "source_url": "https://prometheus.io/docs/prometheus/latest/querying/api/",
    "estimated_doc_count": 3,
    "estimated_size_mb": 0.1,
}
