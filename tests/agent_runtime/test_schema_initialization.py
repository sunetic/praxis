"""Fresh installations contain one runtime schema, without startup migrations."""

from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.models.agent_runs import RUNTIME_TABLES
from app.models.models import DataSource

RETIRED_TABLES = {
    "conversations",
    "messages",
    "conversation_context_snapshots",
    "chat_events",
    "pending_actions",
    "tool_executions",
    "build_sessions",
    "function_build_runs",
    "function_build_events",
}


def test_fresh_init_creates_native_and_business_tables_only(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(database, "engine", engine)
    try:
        database.init_db()
        inspector = inspect(engine)
        names = set(inspector.get_table_names())
        assert not names & RETIRED_TABLES
        assert {table.name for table in RUNTIME_TABLES} <= names
        assert {
            "datasources",
            "agents",
            "functions",
            "schedules",
            "services",
            "function_revisions",
            "artifact_validations",
            "skill_drafts",
        } <= names
        for name in names:
            for key in inspector.get_foreign_keys(name):
                assert key["referred_table"] in names
                assert key["referred_table"] not in RETIRED_TABLES
        schedule_keys = inspector.get_foreign_keys("schedule_runs")
        assert any(key["referred_table"] == "agent_conversations" for key in schedule_keys)
    finally:
        engine.dispose()


def test_repeated_init_does_not_rewrite_business_data(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'repeat.db'}")
    monkeypatch.setattr(database, "engine", engine)
    try:
        database.init_db()
        sessions = sessionmaker(engine)
        with sessions.begin() as db:
            db.add(DataSource(name="retained-source", host="localhost", port=5432))
        statements = []

        @event.listens_for(engine, "before_cursor_execute")
        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.strip().split()[0].upper())

        database.init_db()
        assert not {"ALTER", "DROP", "UPDATE", "DELETE", "INSERT"} & set(statements)
        with sessions() as db:
            assert db.scalars(select(DataSource.name)).all() == ["retained-source"]
    finally:
        engine.dispose()
