from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent.parent / "builtin_skills"
DEFAULT_SQLITE_DB_PATH = DEFAULT_DATA_DIR / "praxis.db"
DEFAULT_TRACING_DB_PATH = DEFAULT_DATA_DIR / "tracing.db"


def _resolve_project_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


def _normalize_sqlite_url(url: str) -> str:
    text = str(url or "").strip()
    prefix = "sqlite:///"
    if not text.startswith(prefix):
        return text
    raw_path = text[len(prefix) :]
    if raw_path in {"", ":memory:"}:
        return text
    return f"{prefix}{_resolve_project_path(raw_path)}"


_ENV_LINE_RE = re.compile(
    r"^(?:export\s+)?(?P<key>[A-Za-z_]\w*)=(?P<value>[^\"'\r\n]*)$",
)


def _read_raw_env_values(path: str | Path = DEFAULT_ENV_FILE) -> dict[str, str]:
    """Read raw values from .env, preserving ``#`` in unquoted values.

    python-dotenv treats ``#`` as an inline comment even inside unquoted
    values.  OceanBase connection strings use ``user@tenant#cluster``,
    so the default parser silently truncates them.
    """
    env_path = Path(path)
    if not env_path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        m = _ENV_LINE_RE.match(line)
        if m and "#" in m.group("value"):
            result[m.group("key").lower()] = m.group("value").rstrip()
    return result


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(DEFAULT_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @model_validator(mode="before")
    @classmethod
    def _patch_dotenv_hash_values(cls, data: dict) -> dict:
        """Fix values truncated by python-dotenv's inline ``#`` comment parsing."""
        raw = _read_raw_env_values()
        for key, raw_value in raw.items():
            if key in data and isinstance(data[key], str) and data[key] != raw_value:
                data[key] = raw_value
        database_url = data.get("database_url")
        if database_url is not None:
            data["database_url"] = _normalize_sqlite_url(str(database_url))
        tracing_db_path = data.get("tracing_db_path")
        if tracing_db_path is not None:
            data["tracing_db_path"] = str(_resolve_project_path(str(tracing_db_path)))
        data_dir = data.get("data_dir")
        if data_dir is not None:
            data["data_dir"] = str(_resolve_project_path(str(data_dir)))
        return data

    app_name: str = "Praxis"
    app_env: str = "development"
    debug: bool = True
    secret_key: str = "dev-secret-key"

    database_url: str = f"sqlite:///{DEFAULT_SQLITE_DB_PATH}"
    data_dir: str = str(DEFAULT_DATA_DIR)
    sqlalchemy_echo: bool = False

    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_model: str = "gpt-4"
    ai_context_char_limit: int = 80_000

    agent_failure_episode_enabled: bool = True
    agent_task_contract_enabled: bool = True
    agent_completion_verifier_enabled: bool = True
    agent_persistent_journal_enabled: bool = True
    agent_parallel_read_only_enabled: bool = True
    agent_adversarial_verification_enabled: bool = True
    agent_max_transient_retries: int = 3
    agent_max_no_progress_rounds: int = 3
    agent_max_verification_retries: int = 5
    agent_max_parallel_tools: int = 4
    agent_transient_backoff_base_seconds: float = 0.5
    agent_transient_backoff_max_seconds: float = 4.0
    agent_max_elapsed_seconds: float = 900.0

    vite_api_base_url: str = "http://localhost:8000/api/v1"
    builder_runtime_enabled: bool = True
    scheduler_autostart: bool = True
    scheduler_refresh_interval_seconds: int = 15
    scheduler_job_coalesce: bool = False
    scheduler_job_misfire_grace_seconds: int = 300
    scheduler_job_max_instances: int = 1

    monitor_db_host: str = ""
    monitor_db_port: int = 2881
    monitor_db_user: str = ""
    monitor_db_password: str = ""
    monitor_db_database: str = "praxis_collector"

    tracing_enabled: bool = True
    tracing_db_path: str = str(DEFAULT_TRACING_DB_PATH)
    tracing_sample_rate: float = 1.0
    tracing_retention_hours: int = 24

    datasource_encryption_key: str = ""

    praxis_edition: str = "community"


@lru_cache
def get_settings() -> Settings:
    return Settings()
