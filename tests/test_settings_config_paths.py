from app.core.config import PROJECT_ROOT, Settings, _normalize_sqlite_url, _resolve_project_path


def test_normalize_sqlite_url_resolves_relative_path_from_project_root():
    normalized = _normalize_sqlite_url("sqlite:///./praxis.db")

    assert normalized == f"sqlite:///{(PROJECT_ROOT / 'praxis.db').resolve()}"


def test_resolve_project_path_keeps_absolute_path():
    absolute = PROJECT_ROOT / "custom.db"

    assert _resolve_project_path(str(absolute)) == absolute.resolve()


def test_deprecated_agent_settings_do_not_break_config_loading():
    settings = Settings(
        _env_file=None,
        agent_soft_finalize_seconds=240,
        agent_max_verification_retries=5,
    )

    assert not hasattr(settings, "agent_soft_finalize_seconds")
    assert not hasattr(settings, "agent_max_verification_retries")
