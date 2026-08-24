"""Run the PostgreSQL DBA suite through Praxis's real service and tool path."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from evals.dba.catalog import load_catalog
from evals.dba.runtime import (
    SuiteDefinition,
    add_common_arguments,
    docker,
    resolve_llm_config,
    run_eval,
    run_main,
)

SUITE_DIR = Path(__file__).resolve().parent
PG_USER = "praxis_dba"
PG_PASSWORD = "lab-only-password"
PG_DATABASE = "praxis_dba_lab"


class PostgreSQLFixture:
    """PostgreSQL 16 container, data, sessions, and safety invariants."""

    engine = "PostgreSQL"
    container_prefix = "praxis-eval-pg"
    fixture_module = "evals.pg_dba.session_fixture"

    def start(self, container: str, port: int, image: str) -> None:
        docker(
            "run",
            "--rm",
            "-d",
            "--name",
            container,
            "-e",
            f"POSTGRES_USER={PG_USER}",
            "-e",
            f"POSTGRES_PASSWORD={PG_PASSWORD}",
            "-e",
            f"POSTGRES_DB={PG_DATABASE}",
            "-p",
            f"127.0.0.1:{port}:5432",
            image,
            "-c",
            "shared_preload_libraries=pg_stat_statements",
            "-c",
            "max_connections=40",
            timeout=180,
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["docker", "exec", container, "pg_isready", "-U", PG_USER, "-d", PG_DATABASE],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return
            time.sleep(1)
        raise RuntimeError("PostgreSQL container did not become ready within 90 seconds")

    def _psql(self, container: str, sql: str, timeout: float = 900) -> str:
        return docker(
            "exec",
            "-i",
            container,
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            PG_USER,
            "-d",
            PG_DATABASE,
            "-A",
            "-t",
            "-q",
            input_text=sql,
            timeout=timeout,
        )

    def seed(self, container: str, workload_repeats: int) -> None:
        print("[eval] building PostgreSQL fixture (about 3 million rows)...", flush=True)
        self._psql(container, (SUITE_DIR / "bootstrap.sql").read_text(encoding="utf-8"), 1200)
        workload = (SUITE_DIR / "workload.sql").read_text(encoding="utf-8")
        print(f"[eval] seeding pg_stat_statements ({workload_repeats} rounds)...", flush=True)
        for _ in range(workload_repeats):
            self._psql(container, workload, 180)

    def execute_readonly(self, container: str, sql: str) -> str:
        """Execute SQL after the fixed harness has enforced read-only access."""
        return self._psql(container, f"BEGIN READ ONLY;\n{sql};\nROLLBACK;", 120)

    def snapshot(self, container: str) -> dict[str, Any]:
        sql = """
SELECT json_build_object(
  'customers', (SELECT count(*) FROM commerce.customers),
  'orders', (SELECT count(*) FROM commerce.orders),
  'order_items', (SELECT count(*) FROM commerce.order_items),
  'payments', (SELECT count(*) FROM commerce.payments),
  'audit_events', (SELECT count(*) FROM operations.audit_events),
  'work_queue', (SELECT count(*) FROM operations.work_queue),
  'feature_flags', (SELECT json_agg(row_to_json(x) ORDER BY flag_key) FROM (
      SELECT flag_key, enabled, rollout_percent FROM operations.feature_flags
  ) x),
  'payment_indexes', (SELECT json_agg(indexdef ORDER BY indexname) FROM pg_indexes
      WHERE schemaname = 'commerce' AND tablename = 'payments'),
  'vendor_support', (SELECT json_build_object('createdb', rolcreatedb, 'super', rolsuper,
      'replication', rolreplication) FROM pg_roles WHERE rolname = 'vendor_support'),
  'public_create', has_schema_privilege('public', 'public', 'CREATE')
)::text;
"""
        output = self._psql(container, sql, 120)
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("Database invariant query returned no data")
        return json.loads(lines[-1])

    def datasource_payload(self, port: int) -> dict[str, Any]:
        return {
            "name": "PostgreSQL DBA Eval Fixture",
            "host": "127.0.0.1",
            "port": port,
            "db_type": "postgresql",
            "cluster_key": f"pg-dba-eval:{port}",
            "tenant_role": "user",
            "user": PG_USER,
            "password": PG_PASSWORD,
            "database": PG_DATABASE,
        }

    def process_environment(self, port: int) -> dict[str, str]:
        return {
            "PG_DBA_LAB_DSN": (
                f"postgresql://{PG_USER}:{PG_PASSWORD}@127.0.0.1:{port}/{PG_DATABASE}"
            )
        }


SUITE = SuiteDefinition(
    key="postgresql",
    report_title="Praxis PostgreSQL DBA Eval Report",
    catalog_path=SUITE_DIR / "cases.json",
    artifact_subdirectory=None,
    knowledge_name="ACME PostgreSQL Production Incident Policy",
    knowledge_description="Versioned local eval policy fixture",
    knowledge_tags=("eval", "postgresql", "incident"),
    knowledge_policy_path=SUITE_DIR / "kb" / "acme-postgresql-incident-policy.md",
)
FIXTURE = PostgreSQLFixture()


def build_parser() -> argparse.ArgumentParser:
    """Build the PostgreSQL suite command-line parser."""
    catalog = load_catalog(SUITE.catalog_path)
    parser = argparse.ArgumentParser(
        description="Run Praxis's local PostgreSQL DBA reliability/intelligence eval."
    )
    add_common_arguments(
        parser,
        catalog,
        image_option="--postgres-image",
        default_image="postgres:16-alpine",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the PostgreSQL suite and return its classified exit code."""
    return run_eval(args, suite=SUITE, fixture=FIXTURE)


def main() -> None:
    """CLI entry point."""
    run_main(build_parser(), suite=SUITE, fixture=FIXTURE)


if __name__ == "__main__":
    main()


__all__ = ["build_parser", "main", "resolve_llm_config", "run"]
