from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Inspector, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger

settings = get_settings()
logger = get_logger("app.db")


def _ensure_sqlite_parent_directory(database_url: str) -> None:
    """Create the parent directory required by a file-backed SQLite URL."""
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return
    Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_parent_directory(settings.database_url)
_is_sqlite = "sqlite" in settings.database_url
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 15} if _is_sqlite else {},
    echo=settings.sqlalchemy_echo,
    # Verify connections before handing them out — avoids stale-connection errors.
    pool_pre_ping=True,
    # SQLite doesn't benefit from a large pool; cap to avoid exhaustion under
    # concurrent scheduler jobs. Non-SQLite deployments inherit their own tuning.
    pool_size=3 if _is_sqlite else 5,
    max_overflow=2 if _is_sqlite else 10,
)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(
        dbapi_connection: Any,
        _connection_record: Any,
    ) -> None:
        """Use SQLite's supported concurrent-reader mode for local multi-stream runs."""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=15000")
        finally:
            cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_agent_schema()
    _migrate_runtime_object_schema()
    _migrate_schedule_schema()
    _migrate_function_schema()
    _migrate_conversation_schema()
    _migrate_message_schema()
    _migrate_chat_event_schema()


def _migrate_agent_schema() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "agents" not in table_names:
        return

    existing_columns = {column["name"] for column in inspector.get_columns("agents")}
    alter_statements: list[str] = []
    if "agent_type" not in existing_columns:
        alter_statements.append(
            "ALTER TABLE agents ADD COLUMN agent_type VARCHAR(50) NOT NULL DEFAULT 'custom'"
        )

    if alter_statements:
        with engine.begin() as conn:
            for stmt in alter_statements:
                conn.execute(text(stmt))

    db = SessionLocal()
    try:
        from app.models import models

        changed = False
        agents = db.query(models.Agent).all()
        for item in agents:
            normalized = str(item.agent_type or "").strip().lower()
            if normalized in {"built_in", "builtin"}:
                normalized = "custom"
            elif normalized != "custom":
                normalized = "custom"
            if item.agent_type != normalized:
                item.agent_type = normalized
                changed = True
        if changed:
            db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("agent_schema_migration_failed %s error=%s", fmt_kv(), str(e))
    finally:
        db.close()


def _migrate_runtime_object_schema() -> None:
    """
    Lightweight runtime migration for object tables introduced in later phases.
    This keeps existing local SQLite databases usable without manual rebuild.
    """
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    column_plan: dict[str, dict[str, str]] = {
        "functions": {
            "slug": "VARCHAR(255)",
            "source_path": "VARCHAR(500)",
            "current_commit_sha": "VARCHAR(64)",
            "release_commit_sha": "VARCHAR(64)",
            "current_release_id": "INTEGER",
        },
        "pages": {
            "source_path": "VARCHAR(500)",
            "current_commit_sha": "VARCHAR(64)",
            "release_commit_sha": "VARCHAR(64)",
            "current_release_id": "INTEGER",
        },
        "schedules": {
            "target_type": "VARCHAR(20)",
            "target_id": "INTEGER",
            "input_payload": "JSON",
            "input_prompt": "TEXT",
            "datasource_id": "INTEGER",
        },
        "schedule_runs": {
            "target_type": "VARCHAR(20)",
            "runtime_run_id": "VARCHAR(64)",
            "runtime_status": "VARCHAR(50)",
            "conversation_id": "INTEGER",
            "output_summary": "TEXT",
            "output_payload": "JSON",
        },
    }

    alter_statements: list[tuple[str, str]] = []
    for table_name, planned_columns in column_plan.items():
        if table_name not in table_names:
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, ddl_type in planned_columns.items():
            if column_name in existing_columns:
                continue
            statement = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_type}"
            alter_statements.append((table_name, statement))

    if not alter_statements:
        return

    with engine.begin() as conn:
        for _, statement in alter_statements:
            conn.execute(text(statement))

        if "functions" in table_names:
            conn.execute(
                text(
                    """
                    UPDATE functions
                    SET slug = name
                    WHERE slug IS NULL OR TRIM(slug) = ''
                    """
                )
            )
            if engine.dialect.name == "sqlite":
                conn.execute(text("DROP INDEX IF EXISTS ux_functions_name"))
            conn.execute(
                text("CREATE UNIQUE INDEX IF NOT EXISTS ux_functions_slug ON functions (slug)")
            )

        if "schedules" in table_names:
            conn.execute(
                text(
                    """
                    UPDATE schedules
                    SET target_type = COALESCE(target_type, 'function'),
                        target_id = COALESCE(target_id, function_id)
                    """
                )
            )
        if "schedule_runs" in table_names:
            conn.execute(
                text(
                    """
                    UPDATE schedule_runs
                    SET target_type = COALESCE(
                        target_type,
                        (SELECT schedules.target_type FROM schedules WHERE schedules.id = schedule_runs.schedule_id)
                    )
                    """
                )
            )

    logger.info(
        "runtime_object_schema_migration_success %s",
        fmt_kv(
            count=len(alter_statements),
            tables=",".join(sorted({table for table, _ in alter_statements})),
        ),
    )


def _migrate_schedule_schema() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "schedules" not in table_names:
        return

    existing_columns = {column["name"] for column in inspector.get_columns("schedules")}
    alter_statements: list[str] = []
    if "description" not in existing_columns:
        alter_statements.append("ALTER TABLE schedules ADD COLUMN description TEXT")
    if "kind" not in existing_columns:
        alter_statements.append(
            "ALTER TABLE schedules ADD COLUMN kind VARCHAR(50) NOT NULL DEFAULT 'custom'"
        )

    if alter_statements:
        with engine.begin() as conn:
            for stmt in alter_statements:
                conn.execute(text(stmt))
            conn.execute(
                text(
                    """
                    UPDATE schedules
                    SET kind = 'built_in'
                    WHERE target_type IN ('stats_analysis', 'collector')
                       OR (
                            target_type = 'function'
                            AND function_id IN (
                                SELECT id FROM functions WHERE LOWER(COALESCE(kind, 'custom')) IN ('built_in', 'builtin')
                            )
                       )
                    """
                )
            )
        logger.info("schedule_schema_migrated %s", fmt_kv(count=len(alter_statements)))

    if _should_rebuild_schedule_table_for_sqlite(inspector):
        _rebuild_schedule_table_for_sqlite()


def _should_rebuild_schedule_table_for_sqlite(inspector: Inspector) -> bool:
    if engine.dialect.name != "sqlite":
        return False
    table_names = set(inspector.get_table_names())
    if "schedules" not in table_names:
        return False
    columns = {column["name"]: column for column in inspector.get_columns("schedules")}
    function_id = columns.get("function_id")
    if not function_id:
        return False
    return not bool(function_id.get("nullable", True))


def _rebuild_schedule_table_for_sqlite() -> None:
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(
            text(
                """
                CREATE TABLE schedules__new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    kind VARCHAR(50) NOT NULL DEFAULT 'custom',
                    status VARCHAR(50) NOT NULL,
                    schedule_type VARCHAR(20) NOT NULL,
                    cron_expression VARCHAR(255),
                    interval_seconds INTEGER,
                    timezone VARCHAR(64) NOT NULL,
                    function_id INTEGER,
                    function_release_id INTEGER,
                    next_run_at DATETIME,
                    last_run_at DATETIME,
                    max_retries INTEGER NOT NULL,
                    retry_backoff_seconds INTEGER NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    target_type VARCHAR(20) NOT NULL DEFAULT 'function',
                    target_id INTEGER,
                    input_payload JSON,
                    input_prompt TEXT,
                    datasource_id INTEGER,
                    FOREIGN KEY(function_id) REFERENCES functions (id),
                    FOREIGN KEY(function_release_id) REFERENCES function_releases (id),
                    FOREIGN KEY(datasource_id) REFERENCES datasources (id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO schedules__new (
                    id, name, description, kind, status, schedule_type, cron_expression, interval_seconds, timezone,
                    function_id, function_release_id, next_run_at, last_run_at,
                    max_retries, retry_backoff_seconds, created_at, updated_at,
                    target_type, target_id, input_payload, input_prompt, datasource_id
                )
                SELECT
                    id,
                    name,
                    description,
                    COALESCE(kind, CASE WHEN target_type IN ('stats_analysis', 'collector') THEN 'built_in' ELSE 'custom' END),
                    status,
                    schedule_type,
                    cron_expression,
                    interval_seconds,
                    timezone,
                    function_id,
                    function_release_id,
                    next_run_at,
                    last_run_at,
                    max_retries,
                    retry_backoff_seconds,
                    created_at,
                    updated_at,
                    COALESCE(target_type, 'function'),
                    target_id,
                    input_payload,
                    input_prompt,
                    datasource_id
                FROM schedules
                """
            )
        )
        conn.execute(text("DROP TABLE schedules"))
        conn.execute(text("ALTER TABLE schedules__new RENAME TO schedules"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_schedules_id ON schedules (id)"))
        conn.execute(text("PRAGMA foreign_keys=ON"))

    logger.info("schedule_schema_rebuilt %s", fmt_kv(dialect="sqlite"))


def _migrate_function_schema() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "functions" not in table_names:
        return
    existing_columns = {col["name"] for col in inspector.get_columns("functions")}
    if "kind" not in existing_columns:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE functions ADD COLUMN kind VARCHAR(50) NOT NULL DEFAULT 'custom'")
            )
        logger.info("function_schema_migrated %s", fmt_kv(added_column="kind"))


def _migrate_conversation_schema() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "conversations" not in table_names:
        return
    existing_columns = {col["name"] for col in inspector.get_columns("conversations")}
    alter_statements: list[str] = []
    if "category" not in existing_columns:
        alter_statements.append(
            "ALTER TABLE conversations ADD COLUMN category VARCHAR(20) NOT NULL DEFAULT 'primary'"
        )
    if "scene_key" not in existing_columns:
        alter_statements.append("ALTER TABLE conversations ADD COLUMN scene_key VARCHAR(100)")
    if "read_only" not in existing_columns:
        alter_statements.append(
            "ALTER TABLE conversations ADD COLUMN read_only BOOLEAN NOT NULL DEFAULT 0"
        )

    if alter_statements:
        with engine.begin() as conn:
            for stmt in alter_statements:
                conn.execute(text(stmt))

    logger.info("conversation_schema_migration_success %s", fmt_kv(count=len(alter_statements)))


def _migrate_message_schema() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "messages" not in table_names:
        return
    existing_columns = {col["name"] for col in inspector.get_columns("messages")}
    if "agent_name" not in existing_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE messages ADD COLUMN agent_name VARCHAR(100)"))


def _migrate_chat_event_schema() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "chat_events" not in table_names:
        return
    existing_columns = {col["name"] for col in inspector.get_columns("chat_events")}
    alter_statements: list[str] = []
    if "turn_id" not in existing_columns:
        alter_statements.append("ALTER TABLE chat_events ADD COLUMN turn_id VARCHAR(64)")
    if "turn_seq" not in existing_columns:
        alter_statements.append("ALTER TABLE chat_events ADD COLUMN turn_seq INTEGER")
    if "part_seq" not in existing_columns:
        alter_statements.append("ALTER TABLE chat_events ADD COLUMN part_seq INTEGER")
    if "role" not in existing_columns:
        alter_statements.append("ALTER TABLE chat_events ADD COLUMN role VARCHAR(20)")
    if "agent_name" not in existing_columns:
        alter_statements.append("ALTER TABLE chat_events ADD COLUMN agent_name VARCHAR(100)")

    if alter_statements:
        with engine.begin() as conn:
            for stmt in alter_statements:
                conn.execute(text(stmt))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_chat_events_turn_seq ON chat_events (conversation_id, turn_seq, part_seq, id)"
                )
            )

    logger.info("chat_event_schema_migration_success %s", fmt_kv(count=len(alter_statements)))
