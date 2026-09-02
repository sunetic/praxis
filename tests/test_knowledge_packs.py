"""Tests for the Knowledge Packs API (manifest, install, uninstall, protection)."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.models import models


@pytest.fixture()
def db_engine(tmp_path: Path):
    db_path = tmp_path / "test_kp.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def db_session_factory(db_engine):
    return sessionmaker(bind=db_engine, autocommit=False, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def db(db_session_factory):
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def manifest_file(tmp_path: Path):
    import json

    manifest = [
        {
            "id": "test-pack-1",
            "name": "Test Pack 1",
            "description": "A test knowledge pack",
            "tags": ["test", "demo"],
            "db_type": "mysql",
            "repo_url": "https://github.com/example/repo.git",
            "branch": "main",
            "subdirectory": "docs",
            "license": "MIT",
            "estimated_doc_count": 10,
            "estimated_size_mb": 1.0,
            "version_pattern": "^[0-9]+\\.[0-9]+$",
            "default_version": "8.4",
        },
        {
            "id": "test-pack-2",
            "name": "Test Pack 2",
            "description": "Another test pack",
            "tags": ["test"],
            "repo_url": "https://github.com/example/repo2.git",
            "branch": "main",
            "subdirectory": "content",
            "license": "Apache-2.0",
            "estimated_doc_count": 5,
            "estimated_size_mb": 0.5,
        },
    ]
    path = tmp_path / "knowledge_packs.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


@pytest.fixture()
def client(db_session_factory, db, manifest_file, tmp_path):
    from app.api import knowledge as k_module
    from app.api import knowledge_packs as kp_module
    from app.db import database as db_mod
    from app.main import app

    def _override_get_db():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[db_mod.get_db] = _override_get_db

    original_manifest = kp_module._MANIFEST_PATH
    original_knowledge_root = k_module.KNOWLEDGE_ROOT
    kp_module._MANIFEST_PATH = manifest_file

    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    k_module.KNOWLEDGE_ROOT = knowledge_root

    installer = kp_module._installer
    installer._data_root = knowledge_root

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        kp_module._MANIFEST_PATH = original_manifest
        k_module.KNOWLEDGE_ROOT = original_knowledge_root


class TestListPacks:
    def test_returns_manifest_entries(self, client: TestClient):
        resp = client.get("/api/v1/knowledge-packs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["id"] == "test-pack-1"
        assert data[0]["status"] == "available"
        assert data[1]["id"] == "test-pack-2"

    def test_shows_installed_status_for_installed_pack(self, client: TestClient, db):
        kb = models.KnowledgeBase(name="Test Pack 1", source="pack", pack_id="test-pack-1")
        db.add(kb)
        db.commit()

        resp = client.get("/api/v1/knowledge-packs")
        assert resp.status_code == 200
        data = resp.json()
        pack1 = next(p for p in data if p["id"] == "test-pack-1")
        assert pack1["status"] == "installed"
        assert pack1["kb_id"] == kb.id


class TestInstallPack:
    def test_unknown_pack_returns_404(self, client: TestClient):
        resp = client.post("/api/v1/knowledge-packs/nonexistent/install")
        assert resp.status_code == 404

    def test_already_installed_returns_409(self, client: TestClient, db):
        kb = models.KnowledgeBase(name="Test Pack 1", source="pack", pack_id="test-pack-1")
        db.add(kb)
        db.commit()

        resp = client.post("/api/v1/knowledge-packs/test-pack-1/install")
        assert resp.status_code == 409

    def test_install_returns_202(self, client: TestClient):
        with patch("app.api.knowledge_packs._installer") as mock_installer:
            mock_installer.install = AsyncMock(return_value=1)

            resp = client.post("/api/v1/knowledge-packs/test-pack-1/install")
            assert resp.status_code == 202
            data = resp.json()
            assert data["pack_id"] == "test-pack-1"
            assert data["status"] == "downloading"


class TestGetPackStatus:
    def test_unknown_pack_returns_404(self, client: TestClient):
        resp = client.get("/api/v1/knowledge-packs/nonexistent/status")
        assert resp.status_code == 404

    def test_available_pack_status(self, client: TestClient):
        resp = client.get("/api/v1/knowledge-packs/test-pack-1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "available"

    def test_installed_pack_status(self, client: TestClient, db):
        kb = models.KnowledgeBase(name="Test Pack 1", source="pack", pack_id="test-pack-1")
        db.add(kb)
        db.commit()

        resp = client.get("/api/v1/knowledge-packs/test-pack-1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "installed"
        assert data["kb_id"] == kb.id

    def test_downloading_pack_status(self, client: TestClient):
        from app.services.knowledge.pack_installer import progress

        progress.set("test-pack-1", "downloading", progress_message="Cloning")
        try:
            resp = client.get("/api/v1/knowledge-packs/test-pack-1/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "downloading"
            assert data["progress_message"] == "Cloning"
        finally:
            progress.clear("test-pack-1")


class TestUninstallPack:
    def test_unknown_pack_returns_404(self, client: TestClient):
        resp = client.delete("/api/v1/knowledge-packs/nonexistent")
        assert resp.status_code == 404

    def test_not_installed_returns_404(self, client: TestClient):
        resp = client.delete("/api/v1/knowledge-packs/test-pack-1")
        assert resp.status_code == 404

    def test_uninstall_succeeds(self, client: TestClient, db):
        kb = models.KnowledgeBase(name="Test Pack 1", source="pack", pack_id="test-pack-1")
        db.add(kb)
        db.commit()

        with patch("app.api.knowledge_packs._installer") as mock_installer:
            mock_installer.uninstall = AsyncMock()
            resp = client.delete("/api/v1/knowledge-packs/test-pack-1")
            assert resp.status_code == 204


class TestPackKBProtection:
    """Pack-installed KBs should be read-only via the standard KB API."""

    @pytest.fixture()
    def pack_kb(self, db, tmp_path):
        kb = models.KnowledgeBase(
            name="Protected Pack KB",
            description="installed via pack",
            source="pack",
            pack_id="test-pack-1",
        )
        db.add(kb)
        db.commit()
        db.refresh(kb)

        kb_dir = tmp_path / "knowledge" / str(kb.id)
        kb_dir.mkdir(parents=True, exist_ok=True)
        return kb

    def test_update_pack_kb_returns_403(self, client: TestClient, pack_kb):
        resp = client.patch(
            f"/api/v1/knowledge-bases/{pack_kb.id}",
            json={"name": "Renamed"},
        )
        assert resp.status_code == 403

    def test_delete_pack_kb_returns_403(self, client: TestClient, pack_kb):
        resp = client.delete(f"/api/v1/knowledge-bases/{pack_kb.id}")
        assert resp.status_code == 403

    def test_upload_to_pack_kb_returns_403(self, client: TestClient, pack_kb):
        resp = client.post(
            f"/api/v1/knowledge-bases/{pack_kb.id}/documents",
            files={"file": ("test.md", b"# Hello\n", "text/markdown")},
        )
        assert resp.status_code == 403

    def test_get_pack_kb_works(self, client: TestClient, pack_kb):
        resp = client.get(f"/api/v1/knowledge-bases/{pack_kb.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "pack"
        assert data["pack_id"] == "test-pack-1"

    def test_list_shows_pack_kb(self, client: TestClient, pack_kb):
        resp = client.get("/api/v1/knowledge-bases")
        assert resp.status_code == 200
        data = resp.json()
        assert any(kb["pack_id"] == "test-pack-1" for kb in data)


class TestUserKBStillEditable:
    """Sanity-check that normal (user-created) KBs are unaffected by pack protection."""

    @pytest.fixture()
    def user_kb(self, db, tmp_path):
        kb = models.KnowledgeBase(name="User KB", source="user")
        db.add(kb)
        db.commit()
        db.refresh(kb)

        kb_dir = tmp_path / "knowledge" / str(kb.id)
        kb_dir.mkdir(parents=True, exist_ok=True)
        return kb

    def test_update_user_kb_works(self, client: TestClient, user_kb):
        resp = client.patch(
            f"/api/v1/knowledge-bases/{user_kb.id}",
            json={"name": "Renamed User KB"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed User KB"

    def test_delete_user_kb_works(self, client: TestClient, user_kb):
        resp = client.delete(f"/api/v1/knowledge-bases/{user_kb.id}")
        assert resp.status_code == 204


class TestListPacksVersionInfo:
    def test_switch_version_endpoint_is_not_exposed(self, client: TestClient):
        resp = client.post(
            "/api/v1/knowledge-packs/test-pack-1/switch-version",
            json={"version": "8.0"},
        )
        assert resp.status_code in {404, 405}

    def test_uninstalled_pack_has_default_version(self, client: TestClient):
        resp = client.get("/api/v1/knowledge-packs")
        assert resp.status_code == 200
        data = resp.json()
        pack1 = next(p for p in data if p["id"] == "test-pack-1")
        assert pack1["default_version"] == "8.4"
        assert pack1["db_type"] == "mysql"
        assert pack1["versions"] is None

    def test_installed_pack_shows_versions_from_meta(self, client: TestClient, db, tmp_path):
        import json

        kb = models.KnowledgeBase(
            name="Test Pack 1",
            source="pack",
            pack_id="test-pack-1",
            version="8.0",
        )
        db.add(kb)
        db.commit()
        db.refresh(kb)

        kb_dir = tmp_path / "knowledge" / str(kb.id)
        kb_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "pack_id": "test-pack-1",
            "db_type": "mysql",
            "version": "8.0",
            "subdirectory": "docs",
            "versions": [
                {"branch": "8.4", "label": "8.4"},
                {"branch": "8.0", "label": "8.0"},
            ],
        }
        (kb_dir / ".kb_meta.json").write_text(json.dumps(meta), encoding="utf-8")

        resp = client.get("/api/v1/knowledge-packs")
        data = resp.json()
        pack1 = next(p for p in data if p["id"] == "test-pack-1")
        assert "current_version" not in pack1
        assert pack1["status"] == "installed"
        assert pack1["versions"] is not None
        assert len(pack1["versions"]) == 2
        assert pack1["versions"][0]["label"] == "8.4"


class TestDiscoverVersions:
    @pytest.mark.asyncio
    async def test_parses_ls_remote_output(self):
        from app.services.knowledge.pack_installer import _discover_versions

        ls_output = (
            "abc123\trefs/heads/8.4\n"
            "def456\trefs/heads/8.0\n"
            "tag000\trefs/tags/8.0\n"
            "tag999\trefs/tags/8.0^{}\n"
            "ghi789\trefs/heads/main\n"
            "jkl012\trefs/heads/5.7\n"
        )

        async def fake_exec(*args, **kwargs):
            class FakeProc:
                returncode = 0

                async def communicate(self):
                    return (ls_output.encode(), b"")

            return FakeProc()

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await _discover_versions("https://example.com/repo.git", r"^[0-9]+\.[0-9]+$")

        assert len(result) == 3
        assert result[0]["label"] == "8.4"
        assert result[1]["label"] == "8.0"
        assert result[1]["ref_type"] == "tag"
        assert result[1]["ref"] == "refs/tags/8.0"
        assert result[1]["commit"] == "tag999"
        assert result[2]["label"] == "5.7"

    @pytest.mark.asyncio
    async def test_returns_empty_on_timeout(self):
        from app.services.knowledge.pack_installer import _discover_versions

        async def fake_exec(*args, **kwargs):
            raise OSError("network unreachable")

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await _discover_versions("https://example.com/repo.git", r"^[0-9]+\.[0-9]+$")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_nonzero_exit(self):
        from app.services.knowledge.pack_installer import _discover_versions

        async def fake_exec(*args, **kwargs):
            class FakeProc:
                returncode = 128

                async def communicate(self):
                    return (b"", b"fatal: could not resolve host")

            return FakeProc()

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await _discover_versions("https://example.com/repo.git", r"^[0-9]+\.[0-9]+$")

        assert result == []


class TestKbMetaAndSearchTools:
    def test_read_kb_meta(self, tmp_path: Path):
        import json

        from app.services.knowledge.search_tools import _DATA_ROOT, read_kb_meta

        kb_dir = tmp_path / "1"
        kb_dir.mkdir()
        meta = {"subdirectory": "docs", "version": "8.4", "pack_id": "test", "db_type": "mysql"}
        (kb_dir / ".kb_meta.json").write_text(json.dumps(meta), encoding="utf-8")

        original = _DATA_ROOT
        import app.services.knowledge.search_tools as st

        st._DATA_ROOT = tmp_path
        try:
            result = read_kb_meta(1)
            assert result is not None
            assert result["subdirectory"] == "docs"
            assert result["version"] == "8.4"
            assert result["db_type"] == "mysql"
        finally:
            st._DATA_ROOT = original

    def test_read_kb_meta_returns_none_for_user_kb(self, tmp_path: Path):
        from app.services.knowledge.search_tools import read_kb_meta

        kb_dir = tmp_path / "2"
        kb_dir.mkdir()

        import app.services.knowledge.search_tools as st

        original = st._DATA_ROOT
        st._DATA_ROOT = tmp_path
        try:
            assert read_kb_meta(2) is None
        finally:
            st._DATA_ROOT = original

    def test_find_kb_by_db_type(self, tmp_path: Path):
        import json

        from app.services.knowledge.search_tools import find_kb_by_db_type

        kb_dir = tmp_path / "5"
        kb_dir.mkdir()
        meta = {
            "subdirectory": "docs",
            "version": "8.4",
            "pack_id": "mysql-test",
            "db_type": "mysql",
        }
        (kb_dir / ".kb_meta.json").write_text(json.dumps(meta), encoding="utf-8")

        import app.services.knowledge.search_tools as st

        original = st._DATA_ROOT
        st._DATA_ROOT = tmp_path
        try:
            assert find_kb_by_db_type("mysql") == 5
            assert find_kb_by_db_type("postgresql") is None
        finally:
            st._DATA_ROOT = original

    def test_resolve_kb_root_with_meta(self, tmp_path: Path):
        import json

        from app.services.knowledge.search_tools import _resolve_kb_root

        kb_dir = tmp_path / "3"
        kb_dir.mkdir()
        docs_dir = kb_dir / "docs"
        docs_dir.mkdir()
        meta = {"subdirectory": "docs", "version": "8.4", "pack_id": "test", "db_type": "mysql"}
        (kb_dir / ".kb_meta.json").write_text(json.dumps(meta), encoding="utf-8")

        import app.services.knowledge.search_tools as st

        original = st._DATA_ROOT
        st._DATA_ROOT = tmp_path
        try:
            root = _resolve_kb_root(3)
            assert root == docs_dir.resolve()
        finally:
            st._DATA_ROOT = original

    def test_resolve_kb_root_without_meta(self, tmp_path: Path):
        from app.services.knowledge.search_tools import _resolve_kb_root

        kb_dir = tmp_path / "4"
        kb_dir.mkdir()

        import app.services.knowledge.search_tools as st

        original = st._DATA_ROOT
        st._DATA_ROOT = tmp_path
        try:
            root = _resolve_kb_root(4)
            assert root == kb_dir.resolve()
        finally:
            st._DATA_ROOT = original
