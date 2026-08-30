"""Run the MySQL DBA suite through Praxis's real service and tool path."""

from __future__ import annotations

from pathlib import Path

from evals.dba_core.runtime import SuiteDefinition, SuiteRunner
from evals.mysql_dba.fixture import MySQLFixture

SUITE_DIR = Path(__file__).resolve().parent
SUITE = SuiteDefinition(
    key="mysql",
    cli_description="Run Praxis's local MySQL DBA reliability/intelligence Eval.",
    image_option="--mysql-image",
    default_image="mysql:8.4",
    report_title="Praxis MySQL DBA Eval Report",
    catalog_path=SUITE_DIR / "cases.json",
    artifact_subdirectory="mysql",
    knowledge_name="ACME MySQL Production Incident Policy",
    knowledge_description="Versioned local Eval policy fixture",
    knowledge_tags=("eval", "mysql", "incident"),
    knowledge_policy_path=SUITE_DIR / "kb" / "acme-mysql-incident-policy.md",
)
FIXTURE = MySQLFixture()
RUNNER = SuiteRunner(suite=SUITE, fixture=FIXTURE)

build_parser = RUNNER.build_parser
run = RUNNER.run
main = RUNNER.main


if __name__ == "__main__":
    main()


__all__ = ["FIXTURE", "RUNNER", "SUITE", "build_parser", "main", "run"]
