from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.models import models
from app.services.function.schema_probe import FunctionSchemaProbe


def _session_factory(tmp_path: Path):
    db_path = tmp_path / "function-schema-probe.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    return factory, engine


def test_function_schema_probe_returns_no_active_datasource_when_empty(tmp_path: Path):
    factory, engine = _session_factory(tmp_path)
    db = factory()
    try:
        probe = FunctionSchemaProbe()
        result = probe.probe(db=db, requirement_text="构建统计查询 Function")
        assert result.ran is False
        assert result.reason == "no_active_datasource"
        assert result.tables == []
        assert result.goal_context == ""
    finally:
        db.close()
        engine.dispose()


def test_function_schema_probe_collects_tables_and_columns(tmp_path: Path, monkeypatch):
    factory, engine = _session_factory(tmp_path)
    db = factory()
    try:
        datasource = models.DataSource(
            name="probe-ds",
            host="127.0.0.1",
            port=2881,
            db_type="oceanbase",
            cluster_key="127.0.0.1:2881",
            tenant_role="user",
            user="test@tenant",
            password="pwd",
            database="test",
            status="active",
        )
        db.add(datasource)
        db.commit()
        db.refresh(datasource)

        class _FakePool:
            async def execute_query(self, datasource, sql, role="user", params=None):
                _ = datasource, role, params
                normalized = str(sql or "").strip().lower()
                if normalized.startswith("show tables"):
                    return {
                        "columns": ["Tables_in_test"],
                        "rows": [
                            {"Tables_in_test": "orders"},
                            {"Tables_in_test": "stats_collection"},
                        ],
                        "row_count": 2,
                    }
                if normalized.startswith("describe `orders`"):
                    return {
                        "columns": ["Field"],
                        "rows": [{"Field": "id"}, {"Field": "tenant_id"}, {"Field": "status"}],
                        "row_count": 3,
                    }
                if normalized.startswith("describe `stats_collection`"):
                    return {
                        "columns": ["Field"],
                        "rows": [{"Field": "table_name"}, {"Field": "last_collected_at"}],
                        "row_count": 2,
                    }
                return {"columns": [], "rows": [], "row_count": 0}

        monkeypatch.setattr("app.services.function.schema_probe.get_db_pool", lambda: _FakePool())

        probe = FunctionSchemaProbe()
        result = probe.probe(db=db, requirement_text="构建统计信息收集状态查询 Function")

        assert result.ran is True
        assert result.reason == "ok"
        assert result.datasource_id == int(datasource.id)
        assert "orders" in result.tables
        assert "stats_collection" in result.tables
        assert result.columns_by_table.get("orders") == ["id", "tenant_id", "status"]
        assert "candidate_tables=orders, stats_collection" in result.goal_context
        assert "orders.columns=id, tenant_id, status" in result.goal_context
        assert len(result.attempts) >= 2
    finally:
        db.close()
        engine.dispose()
