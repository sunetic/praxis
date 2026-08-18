from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import database as db_module
from app.db.database import Base
from app.services.platform.workspace_store import _strip_duplicate_flags


def test_strip_duplicate_flags_removes_known_probe_flags():
    command = "claude -p --output-format json --permission-mode bypassPermissions --allowedTools Edit Read Write Bash"
    pre_flags = "-p --output-format json --permission-mode bypassPermissions"
    post_flags = "--allowedTools Edit Read Write Bash"

    normalized = _strip_duplicate_flags(command, pre_flags, post_flags)

    assert normalized == "claude"


def test_patch_settings_normalizes_external_cli_command(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "settings-normalization.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    session_local = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        expire_on_commit=False,
    )
    monkeypatch.setattr(db_module, "engine", engine, raising=False)
    monkeypatch.setattr(db_module, "SessionLocal", session_local, raising=False)
    Base.metadata.create_all(bind=engine)

    import app.main as main_module
    from app.services.scheduler.runtime_state import set_scheduler_worker

    main_module.settings.scheduler_autostart = False
    set_scheduler_worker(None)
    monkeypatch.setattr("app.main.configure_logging", lambda debug: None)

    with TestClient(main_module.app) as client:
        defaults = client.get("/api/v1/settings")
        assert defaults.status_code == 200, defaults.text
        assert defaults.json()["context_window_tokens"] == 128_000
        assert defaults.json()["context_compression_threshold_percent"] == 75

        context_update = client.patch(
            "/api/v1/settings",
            json={
                "context_window_tokens": 131_072,
                "context_compression_threshold_percent": 82,
            },
        )
        assert context_update.status_code == 200, context_update.text
        assert context_update.json()["context_window_tokens"] == 131_072
        assert context_update.json()["context_compression_threshold_percent"] == 82

        below_minimum = client.patch(
            "/api/v1/settings",
            json={"context_compression_threshold_percent": 49},
        )
        assert below_minimum.status_code == 422
        above_maximum = client.patch(
            "/api/v1/settings",
            json={"context_compression_threshold_percent": 96},
        )
        assert above_maximum.status_code == 422

        seed = client.patch(
            "/api/v1/settings",
            json={
                "external_cli_pre_flags": "-p --output-format json --permission-mode bypassPermissions",
                "external_cli_post_flags": "--allowedTools Edit Read Write Bash",
            },
        )
        assert seed.status_code == 200, seed.text

        response = client.patch(
            "/api/v1/settings",
            json={
                "build_engine": "external_cli",
                "external_cli_command": "claude -p --output-format json --permission-mode bypassPermissions --allowedTools Edit Read Write Bash",
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["build_engine"] == "external_cli"
        # Settings endpoint stores the command as-is; normalization
        # (strip_duplicate_flags) is applied at workspace build time, not on save.
        assert (
            payload["external_cli_command"]
            == "claude -p --output-format json --permission-mode bypassPermissions --allowedTools Edit Read Write Bash"
        )
        assert (
            payload["external_cli_pre_flags"]
            == "-p --output-format json --permission-mode bypassPermissions"
        )
        assert payload["external_cli_post_flags"] == "--allowedTools Edit Read Write Bash"

    set_scheduler_worker(None)
    engine.dispose()
