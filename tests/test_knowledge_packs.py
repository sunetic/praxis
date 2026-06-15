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
            "repo_url": "https://github.com/example/repo.git",
            "branch": "main",
            "subdirectory": "docs",
            "license": "MIT",
            "estimated_doc_count": 10,
            "estimated_size_mb": 1.0,
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
    from app.api import knowledge_packs as kp_module
    from app.api import knowledge as k_module
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
