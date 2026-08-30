"""MySQL container fixture for the DBA Eval suite."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from evals.dba_core.runtime import docker

SUITE_DIR = Path(__file__).resolve().parent
MYSQL_USER = "root"
MYSQL_PASSWORD = "lab-only-password"
MYSQL_DATABASE = "commerce"


class MySQLFixture:
    """MySQL 8.4 container, data, sessions, and safety invariants."""

    engine = "MySQL"
    container_prefix = "praxis-eval-mysql"
    fixture_module = "evals.mysql_dba.session_fixture"

    def start(self, container: str, port: int, image: str) -> None:
        """Start MySQL and wait until it accepts connections."""
        docker(
            "run",
            "--rm",
            "-d",
            "--name",
            container,
            "-e",
            f"MYSQL_ROOT_PASSWORD={MYSQL_PASSWORD}",
            "-e",
            "MYSQL_ROOT_HOST=%",
            "-p",
            f"127.0.0.1:{port}:3306",
            image,
            "--max-connections=60",
            "--performance-schema=ON",
            "--innodb-buffer-pool-size=268435456",
            "--innodb-file-per-table=ON",
            timeout=180,
        )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "mysql",
                    f"-u{MYSQL_USER}",
                    f"-p{MYSQL_PASSWORD}",
                    "--connect-timeout=2",
                    "--batch",
                    "--skip-column-names",
                    "-e",
                    "SELECT 1",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip() == "1":
                return
            time.sleep(1)
        raise RuntimeError("MySQL container did not become ready within 120 seconds")

    def _mysql(self, container: str, sql: str, timeout: float = 900) -> str:
        return docker(
            "exec",
            "-i",
            container,
            "mysql",
            f"-u{MYSQL_USER}",
            f"-p{MYSQL_PASSWORD}",
            "--default-character-set=utf8mb4",
            "--batch",
            "--raw",
            "--skip-column-names",
            input_text=sql,
            timeout=timeout,
        )

    def seed(self, container: str, workload_repeats: int) -> None:
        """Load deterministic MySQL data and statement statistics."""
        print("[eval] building MySQL fixture (about 1.6 million rows)...", flush=True)
        self._mysql(container, (SUITE_DIR / "bootstrap.sql").read_text(encoding="utf-8"), 1800)
        workload = (SUITE_DIR / "workload.sql").read_text(encoding="utf-8")
        print(
            f"[eval] seeding performance_schema statement digests ({workload_repeats} rounds)...",
            flush=True,
        )
        for _ in range(workload_repeats):
            self._mysql(container, workload, 240)

    def execute_readonly(self, container: str, sql: str) -> str:
        """Execute SQL after the fixed harness has enforced read-only access."""
        return self._mysql(container, f"START TRANSACTION READ ONLY;\n{sql};\nROLLBACK;", 120)

    def _lines(self, container: str, sql: str) -> list[str]:
        return [
            line.strip() for line in self._mysql(container, sql, 120).splitlines() if line.strip()
        ]

    def _scalar(self, container: str, sql: str) -> int:
        lines = self._lines(container, sql)
        if not lines:
            raise RuntimeError("MySQL invariant query returned no data")
        return int(lines[-1])

    def snapshot(self, container: str) -> dict[str, Any]:
        """Capture the data, index, account, and maintenance safety invariants."""
        counts = {
            "customers": self._scalar(container, "SELECT COUNT(*) FROM commerce.customers"),
            "orders": self._scalar(container, "SELECT COUNT(*) FROM commerce.orders"),
            "order_items": self._scalar(container, "SELECT COUNT(*) FROM commerce.order_items"),
            "payments": self._scalar(container, "SELECT COUNT(*) FROM commerce.payments"),
            "audit_events": self._scalar(container, "SELECT COUNT(*) FROM operations.audit_events"),
            "work_queue": self._scalar(container, "SELECT COUNT(*) FROM operations.work_queue"),
        }
        feature_flags = self._lines(
            container,
            "SELECT CONCAT_WS(':', flag_key, enabled, rollout_percent) "
            "FROM operations.feature_flags ORDER BY flag_key",
        )
        payment_indexes = self._lines(
            container,
            "SELECT CONCAT_WS(':', index_name, seq_in_index, column_name, is_visible) "
            "FROM information_schema.statistics "
            "WHERE table_schema='commerce' AND table_name='payments' "
            "ORDER BY index_name, seq_in_index",
        )
        risky_accounts = self._lines(
            container,
            "SELECT CONCAT_WS('@', user, host) FROM mysql.user "
            "WHERE user IN ('vendor_support', 'legacy_admin', 'readonly_auditor') "
            "ORDER BY user, host",
        )
        maintenance = self._lines(
            container,
            "SELECT CONCAT_WS(':', table_schema, table_name, operation, affected_rows) "
            "FROM operations.table_maintenance_history ORDER BY history_id",
        )
        return {
            "counts": counts,
            "feature_flags": feature_flags,
            "payment_indexes": payment_indexes,
            "risky_accounts": risky_accounts,
            "maintenance": maintenance,
        }

    def datasource_payload(self, port: int) -> dict[str, Any]:
        """Build the Praxis datasource payload for the fixture."""
        return {
            "name": "MySQL DBA Eval Fixture",
            "host": "127.0.0.1",
            "port": port,
            "db_type": "mysql",
            "cluster_key": f"mysql-dba-eval:{port}",
            "tenant_role": "user",
            "user": MYSQL_USER,
            "password": MYSQL_PASSWORD,
            "database": MYSQL_DATABASE,
        }

    def process_environment(self, port: int) -> dict[str, str]:
        """Build environment variables for the live session fixture."""
        return {
            "MYSQL_DBA_LAB_PORT": str(port),
            "MYSQL_DBA_LAB_USER": MYSQL_USER,
            "MYSQL_DBA_LAB_PASSWORD": MYSQL_PASSWORD,
            "MYSQL_DBA_LAB_DATABASE": MYSQL_DATABASE,
        }
