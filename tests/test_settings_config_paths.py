from app.core.config import PROJECT_ROOT, _normalize_sqlite_url, _resolve_project_path


def test_normalize_sqlite_url_resolves_relative_path_from_project_root():
    normalized = _normalize_sqlite_url("sqlite:///./praxis.db")

    assert normalized == f"sqlite:///{(PROJECT_ROOT / 'praxis.db').resolve()}"


def test_resolve_project_path_keeps_absolute_path():
    absolute = PROJECT_ROOT / "custom.db"

    assert _resolve_project_path(str(absolute)) == absolute.resolve()
