from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base

settings = get_settings()


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


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create the current schema for a new installation; no legacy migrations."""
    from app.models import artifacts, models  # noqa: F401

    Base.metadata.create_all(bind=engine)
