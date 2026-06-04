from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.models import models
from app.services.function.strategy import (
    FunctionCandidateRetriever,
    FunctionStrategyDecider,
    FunctionVerificationHarness,
    StrategyThresholds,
)


@pytest.fixture
def session_factory(tmp_path: Path):
    db_path = tmp_path / "function-strategy.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _create_released_function(
    db,
    *,
    name: str,
    description: str,
    release_metadata: dict | None = None,
) -> models.Function:
    fn = models.Function(name=name, description=description, status="released")
    db.add(fn)
    db.flush()
    release = models.FunctionRelease(
        function_id=fn.id,
        version=1,
        code_snapshot="result = {'ok': True}",
        release_metadata=release_metadata or {},
    )
    db.add(release)
    db.flush()
    fn.current_release_id = release.id
    db.commit()
    db.refresh(fn)
    db.refresh(release)
    return fn


def test_candidate_retrieval_prioritizes_released_similarity(session_factory):
    db = session_factory()
    try:
        _create_released_function(
            db,
            name="slow-sql-analysis",
            description="Analyze slow SQL latency and wait events",
            release_metadata={"contract": {"output": "table"}},
        )
        _create_released_function(
            db,
            name="cluster-health-report",
            description="Check cluster health summary",
            release_metadata={"contract": {"output": "summary"}},
        )
        retriever = FunctionCandidateRetriever()
        candidates = retriever.retrieve(
            db,
            requirement_text="Need slow SQL latency analysis dashboard",
            contract={"output": "table"},
        )
        assert candidates
        assert candidates[0]["name"] == "slow-sql-analysis"
        assert candidates[0]["score"] >= candidates[-1]["score"]
    finally:
        db.close()


def test_strategy_decision_respects_threshold_controls(session_factory):
    db = session_factory()
    try:
        target = _create_released_function(
            db,
            name="slow-sql-analysis",
            description="Analyze slow SQL latency and wait events",
        )
        decider = FunctionStrategyDecider()

        forced = decider.decide(
            db,
            requirement_text="unrelated",
            exclude_function_id=None,
            force_strategy="create",
        )
        assert forced["strategy"] == "create"
        assert forced["reason"] == "forced_by_input"

        reuse = decider.decide(
            db,
            requirement_text="slow sql analysis latency",
            exclude_function_id=None,
            thresholds=StrategyThresholds(reuse=0.1, extend=0.05),
        )
        assert reuse["strategy"] == "reuse"
        assert reuse["top_candidate"]["function_id"] == target.id
    finally:
        db.close()


def test_verification_harness_returns_actionable_diagnostics():
    harness = FunctionVerificationHarness()

    failed = harness.verify_draft(
        code_snapshot="def broken(",
        dependency_manifest={"requests": "2.0"},
    )
    assert failed["passed"] is False
    assert failed["diagnostics"]
    assert any("Syntax error" in detail for detail in failed["diagnostics"])

    passed = harness.verify_draft(
        code_snapshot="def main(payload, context):\n    return {'ok': True}",
        dependency_manifest={"requests": "2.0"},
    )
    assert passed["passed"] is True
    assert passed["diagnostics"] == []


def test_verification_harness_rejects_direct_iteration_of_db_query_result():
    harness = FunctionVerificationHarness()

    failed = harness.verify_draft(
        code_snapshot=(
            "def main(payload, context):\n"
            "    rows = db.query('SHOW DATABASES', datasource=1)\n"
            "    names = [row[0] for row in rows]\n"
            "    return {'names': names}\n"
        ),
        dependency_manifest={},
    )
    assert failed["passed"] is False
    assert any("db.query" in detail for detail in failed["diagnostics"])

    failed_row_index = harness.verify_draft(
        code_snapshot=(
            "def main(payload, context):\n"
            "    query_result = db.query('SHOW DATABASES', datasource=1)\n"
            "    rows = query_result.get('rows', [])\n"
            "    names = [row[0] for row in rows]\n"
            "    return {'names': names}\n"
        ),
        dependency_manifest={},
    )
    assert failed_row_index["passed"] is False
    assert any("row[0]" in detail for detail in failed_row_index["diagnostics"])

    passed = harness.verify_draft(
        code_snapshot=(
            "def main(payload, context):\n"
            "    query_result = db.query('SHOW DATABASES', datasource=1)\n"
            "    rows = query_result.get('rows', [])\n"
            "    names = [row.get('Database') for row in rows if isinstance(row, dict)]\n"
            "    return {'names': names}\n"
        ),
        dependency_manifest={},
    )
    assert passed["passed"] is True

    reassigned = harness.verify_draft(
        code_snapshot=(
            "def main(payload, context):\n"
            "    rows = db.query('SHOW DATABASES', datasource=1)\n"
            "    rows = rows.get('rows', [])\n"
            "    names = [row.get('Database') for row in rows if isinstance(row, dict)]\n"
            "    return {'names': names}\n"
        ),
        dependency_manifest={},
    )
    assert reassigned["passed"] is True


def test_verification_harness_rejects_by_id_positional_calling():
    harness = FunctionVerificationHarness()
    failed = harness.verify_draft(
        code_snapshot=(
            "def main(payload, context):\n"
            "    result = db.query_by_id('select 1', 1)\n"
            "    return {'rows': result.get('rows', [])}\n"
        ),
        dependency_manifest={},
    )
    assert failed["passed"] is False
    assert any("datasource_id" in detail for detail in failed["diagnostics"])


def test_verification_harness_rejects_swallowed_db_exceptions():
    harness = FunctionVerificationHarness()
    failed = harness.verify_draft(
        code_snapshot=(
            "def main(payload, context):\n"
            "    try:\n"
            "        db.query('SHOW DATABASES', datasource=1)\n"
            "    except Exception:\n"
            "        return {'rows': []}\n"
            "    return {'ok': True}\n"
        ),
        dependency_manifest={},
    )
    assert failed["passed"] is False
    assert any("不能吞异常" in detail for detail in failed["diagnostics"])


def test_verification_harness_allows_db_exceptions_to_be_reraised():
    harness = FunctionVerificationHarness()
    passed = harness.verify_draft(
        code_snapshot=(
            "def main(payload, context):\n"
            "    try:\n"
            "        db.query('SHOW DATABASES', datasource=1)\n"
            "    except Exception as err:\n"
            "        raise RuntimeError('query failed') from err\n"
            "    return {'ok': True}\n"
        ),
        dependency_manifest={},
    )
    assert passed["passed"] is True


def test_verification_harness_rejects_undeclared_scheduler_history_params():
    harness = FunctionVerificationHarness()
    failed = harness.verify_draft(
        code_snapshot=(
            "def main(payload, context):\n"
            "    return scheduler_history.delete(\n"
            "        schedule_id=payload['schedule_id'],\n"
            "        retention_hours=168,\n"
            "        dry_run=True,\n"
            "    )\n"
        ),
        dependency_manifest={},
    )
    assert failed["passed"] is False
    assert any("未声明参数" in detail for detail in failed["diagnostics"])


def test_verification_harness_accepts_structured_scheduler_history_contract():
    harness = FunctionVerificationHarness()
    passed = harness.verify_draft(
        code_snapshot=(
            "def main(payload, context):\n"
            "    return scheduler_history.delete(\n"
            "        where={'schedule_id': int(payload['schedule_id']), 'statuses': ['success', 'failed']},\n"
            "        policy={'retention_seconds': int(payload.get('retention_seconds', 86400))},\n"
            "        dry_run=bool(payload.get('dry_run', True)),\n"
            "    )\n"
        ),
        dependency_manifest={},
    )
    assert passed["passed"] is True


def test_verification_harness_rejects_invalid_db_role_and_undeclared_platform_payload():
    harness = FunctionVerificationHarness()
    failed = harness.verify_draft(
        code_snapshot=(
            "def main(payload, context):\n"
            "    db.query('select 1', datasource=1, role='system')\n"
            "    return platform.crud(\n"
            "        object_type='scheduler',\n"
            "        action='create',\n"
            "        payload={'function_id': 1, 'schedule_type': 'cron'},\n"
            "    )\n"
        ),
        dependency_manifest={},
    )
    assert failed["passed"] is False
    assert any("db.query.role" in detail for detail in failed["diagnostics"])
    assert any("platform.crud(scheduler.create).payload" in detail for detail in failed["diagnostics"])


def test_verification_harness_accepts_canonical_platform_scheduler_create_payload():
    harness = FunctionVerificationHarness()
    passed = harness.verify_draft(
        code_snapshot=(
            "def main(payload, context):\n"
            "    return platform.crud(\n"
            "        object_type='scheduler',\n"
            "        action='create',\n"
            "        payload={\n"
            "            'name': 'daily-cleanup',\n"
            "            'target_type': 'function',\n"
            "            'target_id': 1,\n"
            "            'schedule_type': 'cron',\n"
            "            'cron_expression': '0 3 * * *',\n"
            "            'status': 'active',\n"
            "        },\n"
            "    )\n"
        ),
        dependency_manifest={},
    )
    assert passed["passed"] is True
