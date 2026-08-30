"""Run the PostgreSQL DBA suite through Praxis's real service and tool path."""

from __future__ import annotations

from pathlib import Path

from evals.dba_core.runtime import SuiteDefinition, SuiteRunner
from evals.pg_dba.fixture import PostgreSQLFixture

SUITE_DIR = Path(__file__).resolve().parent
SUITE = SuiteDefinition(
    key="postgresql",
    cli_description="Run Praxis's local PostgreSQL DBA reliability/intelligence Eval.",
    image_option="--postgres-image",
    default_image="postgres:16-alpine",
    report_title="Praxis PostgreSQL DBA Eval Report",
    catalog_path=SUITE_DIR / "cases.json",
    artifact_subdirectory=None,
    knowledge_name="ACME PostgreSQL Production Incident Policy",
    knowledge_description="Versioned local Eval policy fixture",
    knowledge_tags=("eval", "postgresql", "incident"),
    knowledge_policy_path=SUITE_DIR / "kb" / "acme-postgresql-incident-policy.md",
)
FIXTURE = PostgreSQLFixture()
RUNNER = SuiteRunner(suite=SUITE, fixture=FIXTURE)

build_parser = RUNNER.build_parser
run = RUNNER.run
main = RUNNER.main


if __name__ == "__main__":
    main()


__all__ = ["FIXTURE", "RUNNER", "SUITE", "build_parser", "main", "run"]
