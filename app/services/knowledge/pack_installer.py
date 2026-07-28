from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any

from app.core.config import DEFAULT_DATA_DIR
from app.core.logging import fmt_kv, get_logger

logger = get_logger("knowledge.pack_installer")

_CLONE_TIMEOUT = 300
_CHECKOUT_TIMEOUT = 60
_LS_REMOTE_TIMEOUT = 30
_KB_META_FILE = ".kb_meta.json"


class PackInstallProgress:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    def get(self, pack_id: str) -> dict[str, Any] | None:
        return self._store.get(pack_id)

    def set(self, pack_id: str, status: str, **kwargs: Any) -> None:
        self._store[pack_id] = {"pack_id": pack_id, "status": status, **kwargs}

    def clear(self, pack_id: str) -> None:
        self._store.pop(pack_id, None)


progress = PackInstallProgress()


async def _run_git(args: list[str], cwd: str | Path, timeout: int = _CHECKOUT_TIMEOUT) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def _check_git_available() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)
        return proc.returncode == 0
    except (FileNotFoundError, asyncio.TimeoutError):
        return False


def _write_kb_meta(
    kb_dir: Path,
    pack_id: str,
    db_type: str | None,
    version: str | None,
    subdirectory: str,
    versions: list[dict[str, str]] | None = None,
) -> None:
    meta: dict[str, Any] = {
        "pack_id": pack_id,
        "db_type": db_type,
        "version": version,
        "subdirectory": subdirectory,
    }
    if versions is not None:
        meta["versions"] = versions
    (kb_dir / _KB_META_FILE).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def read_kb_meta(kb_dir: Path) -> dict[str, Any] | None:
    meta_file = kb_dir / _KB_META_FILE
    if not meta_file.is_file():
        return None
    try:
        return json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_install_branch(pack: dict[str, Any]) -> str:
    default_version = pack.get("default_version")
    if default_version:
        return default_version
    return pack["branch"]


async def _discover_versions(repo_url: str, version_pattern: str) -> list[dict[str, str]]:
    """Run git ls-remote to discover branches matching version_pattern."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "ls-remote", "--heads", repo_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_LS_REMOTE_TIMEOUT)
    except (asyncio.TimeoutError, OSError) as e:
        logger.warning("ls-remote failed for %s: %s", repo_url, e)
        return []

    if proc.returncode != 0:
        return []

    pattern = re.compile(version_pattern)
    versions: list[dict[str, str]] = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        ref = parts[1]
        branch_name = ref.removeprefix("refs/heads/")
        if pattern.match(branch_name):
            versions.append({"branch": branch_name, "label": branch_name})

    versions.sort(key=lambda v: v["label"], reverse=True)
    return versions


class PackInstaller:
    def __init__(self) -> None:
        self._data_root = DEFAULT_DATA_DIR / "knowledge"

    async def install(self, pack: dict[str, Any], db_session_factory: Any) -> int:
        if pack.get("type") == "local":
            return await self._install_local(pack, db_session_factory)

        pack_id = pack["id"]
        progress.set(pack_id, "downloading", progress_message="Checking git availability")

        if not await _check_git_available():
            progress.set(pack_id, "error", error_message="git is not installed on the server")
            raise RuntimeError("git is not installed on the server")

        from app.models import models

        db = db_session_factory()
        kb = None
        kb_dir: Path | None = None
        try:
            branch = _resolve_install_branch(pack)
            version_label = pack.get("default_version")
            subdirectory = pack["subdirectory"]

            discovered_versions: list[dict[str, str]] | None = None
            version_pattern = pack.get("version_pattern")
            if version_pattern:
                progress.set(pack_id, "downloading", progress_message="Discovering versions")
                discovered_versions = await _discover_versions(pack["repo_url"], version_pattern)
                if not discovered_versions:
                    discovered_versions = [{"branch": branch, "label": version_label or branch}]

            kb = models.KnowledgeBase(
                name=pack["name"],
                description=pack["description"],
                tags=pack.get("tags"),
                source="pack",
                pack_id=pack_id,
                version=version_label,
                repo_subdirectory=subdirectory,
            )
            db.add(kb)
            db.commit()
            db.refresh(kb)

            kb_dir = self._data_root / str(kb.id)

            progress.set(pack_id, "downloading", progress_message="Cloning repository")

            kb_dir.parent.mkdir(parents=True, exist_ok=True)
            rc, _, err = await _run_git(
                ["clone", "--filter=blob:none", "--no-checkout", "--depth", "1",
                 "--branch", branch, pack["repo_url"], str(kb_dir.resolve())],
                cwd=str(self._data_root),
                timeout=_CLONE_TIMEOUT,
            )
            if rc != 0:
                raise RuntimeError(f"git clone failed: {err[:500]}")

            progress.set(pack_id, "downloading", progress_message="Setting up sparse checkout")

            rc, _, err = await _run_git(
                ["sparse-checkout", "init", "--cone"],
                cwd=kb_dir,
            )
            if rc != 0:
                raise RuntimeError(f"sparse-checkout init failed: {err[:500]}")

            rc, _, err = await _run_git(
                ["sparse-checkout", "set", subdirectory],
                cwd=kb_dir,
            )
            if rc != 0:
                raise RuntimeError(f"sparse-checkout set failed: {err[:500]}")

            rc, _, err = await _run_git(
                ["checkout"],
                cwd=kb_dir,
                timeout=_CLONE_TIMEOUT,
            )
            if rc != 0:
                raise RuntimeError(f"git checkout failed: {err[:500]}")

            progress.set(pack_id, "downloading", progress_message="Scanning documents")

            src_dir = kb_dir / subdirectory
            if not src_dir.is_dir():
                raise RuntimeError(f"Subdirectory not found after checkout: {subdirectory}")

            md_files = sorted(
                p for p in src_dir.rglob("*.md")
                if p.name.lower() != "readme.md"
            )
            if not md_files:
                raise RuntimeError("No .md files found in the specified subdirectory")

            progress.set(
                pack_id, "downloading",
                progress_message=f"Creating knowledge base ({len(md_files)} documents)",
            )

            _write_kb_meta(kb_dir, pack_id, pack.get("db_type"), version_label, subdirectory, discovered_versions)

            for md_path in md_files:
                rel = md_path.relative_to(src_dir)
                doc = models.KnowledgeDocument(
                    kb_id=kb.id,
                    title=md_path.stem.replace("-", " ").replace("_", " "),
                    filename=str(rel),
                    content_path=str(md_path),
                    size_bytes=md_path.stat().st_size,
                )
                db.add(doc)

            db.commit()
            logger.info(
                "pack_install_complete %s",
                fmt_kv(pack_id=pack_id, kb_id=kb.id, doc_count=len(md_files)),
            )
            progress.set(pack_id, "installed", kb_id=kb.id)
            return kb.id
        except Exception as e:
            if kb and kb.id:
                db.rollback()
                try:
                    db.delete(kb)
                    db.commit()
                except Exception:
                    db.rollback()
            if kb_dir and kb_dir.exists():
                shutil.rmtree(kb_dir, ignore_errors=True)
            error_msg = str(e)[:500]
            if progress.get(pack_id) and progress.get(pack_id, {}).get("status") != "installed":
                progress.set(pack_id, "error", error_message=error_msg)
            logger.exception("pack_install_failed %s", fmt_kv(pack_id=pack_id))
            raise
        finally:
            db.close()

    async def _install_local(self, pack: dict[str, Any], db_session_factory: Any) -> int:
        pack_id = pack["id"]
        raw = pack["local_path"]
        local_path = Path(raw) if Path(raw).is_absolute() else DEFAULT_DATA_DIR.parent / raw

        if not local_path.is_dir():
            progress.set(pack_id, "error", error_message=f"Local path not found: {local_path}")
            raise RuntimeError(f"Local path not found: {local_path}")

        from app.models import models

        db = db_session_factory()
        kb = None
        kb_dir: Path | None = None
        try:
            md_files = sorted(
                p for p in local_path.rglob("*.md")
                if p.name.lower() != "readme.md"
            )
            if not md_files:
                raise RuntimeError("No .md files found in local path")

            tags = pack.get("tags")
            if isinstance(tags, list):
                tags_value = tags
            elif isinstance(tags, str):
                tags_value = [t.strip() for t in tags.split(",") if t.strip()]
            else:
                tags_value = None

            kb = models.KnowledgeBase(
                name=pack["name"],
                description=pack["description"],
                tags=tags_value,
                source="pack",
                pack_id=pack_id,
                version="bundled",
                repo_subdirectory="",
            )
            db.add(kb)
            db.commit()
            db.refresh(kb)

            kb_dir = self._data_root / str(kb.id)
            kb_dir.mkdir(parents=True, exist_ok=True)

            meta: dict[str, Any] = {
                "pack_id": pack_id,
                "db_type": pack.get("db_type"),
                "version": "bundled",
                "subdirectory": "",
                "local_path": str(local_path),
            }
            (kb_dir / _KB_META_FILE).write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8"
            )

            for md_path in md_files:
                rel = md_path.relative_to(local_path)
                doc = models.KnowledgeDocument(
                    kb_id=kb.id,
                    title=md_path.stem.replace("-", " ").replace("_", " "),
                    filename=str(rel),
                    content_path=str(md_path),
                    size_bytes=md_path.stat().st_size,
                )
                db.add(doc)

            db.commit()
            logger.info(
                "local_pack_install_complete %s",
                fmt_kv(pack_id=pack_id, kb_id=kb.id, doc_count=len(md_files)),
            )
            progress.set(pack_id, "installed", kb_id=kb.id)
            return kb.id
        except Exception as e:
            if kb and kb.id:
                db.rollback()
                try:
                    db.delete(kb)
                    db.commit()
                except Exception:
                    db.rollback()
            if kb_dir and kb_dir.exists():
                shutil.rmtree(kb_dir, ignore_errors=True)
            error_msg = str(e)[:500]
            progress.set(pack_id, "error", error_message=error_msg)
            logger.exception("local_pack_install_failed %s", fmt_kv(pack_id=pack_id))
            raise
        finally:
            db.close()

    async def switch_version(
        self,
        pack_id: str,
        target_version: str,
        manifest_pack: dict[str, Any],
        db_session_factory: Any,
    ) -> dict[str, Any]:
        target_branch = target_version

        from app.models import models

        db = db_session_factory()
        try:
            kb = (
                db.query(models.KnowledgeBase)
                .filter(models.KnowledgeBase.pack_id == pack_id, models.KnowledgeBase.source == "pack")
                .first()
            )
            if not kb:
                raise ValueError(f"Pack '{pack_id}' is not installed")

            if kb.version == target_version:
                doc_count = db.query(models.KnowledgeDocument).filter(
                    models.KnowledgeDocument.kb_id == kb.id
                ).count()
                return {"pack_id": pack_id, "version": target_version, "doc_count": doc_count}

            kb_dir = self._data_root / str(kb.id)
            if not (kb_dir / ".git").is_dir():
                raise RuntimeError(
                    f"Pack '{pack_id}' was installed without git history. "
                    "Please uninstall and reinstall to enable version switching."
                )

            subdirectory = kb.repo_subdirectory or manifest_pack.get("subdirectory", "")

            try:
                rc, _, err = await _run_git(
                    ["fetch", "--depth", "1", "origin", target_branch],
                    cwd=kb_dir,
                    timeout=_CLONE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"Network timeout while fetching version '{target_version}'. "
                    "Please check your internet connection and try again."
                )
            if rc != 0:
                if "Could not resolve host" in err or "unable to access" in err.lower():
                    raise RuntimeError(
                        f"Cannot reach remote repository to fetch version '{target_version}'. "
                        "Please check your internet connection and try again."
                    )
                raise RuntimeError(f"Failed to fetch version '{target_version}': {err[:300]}")

            rc, _, err = await _run_git(
                ["checkout", "FETCH_HEAD"],
                cwd=kb_dir,
                timeout=_CHECKOUT_TIMEOUT,
            )
            if rc != 0:
                raise RuntimeError(f"git checkout failed: {err[:500]}")

            src_dir = kb_dir / subdirectory if subdirectory else kb_dir
            md_files = sorted(
                p for p in src_dir.rglob("*.md")
                if p.name.lower() != "readme.md"
            )

            db.query(models.KnowledgeDocument).filter(
                models.KnowledgeDocument.kb_id == kb.id
            ).delete()

            for md_path in md_files:
                rel = md_path.relative_to(src_dir)
                doc = models.KnowledgeDocument(
                    kb_id=kb.id,
                    title=md_path.stem.replace("-", " ").replace("_", " "),
                    filename=str(rel),
                    content_path=str(md_path),
                    size_bytes=md_path.stat().st_size,
                )
                db.add(doc)

            kb.version = target_version
            db.commit()

            existing_meta = read_kb_meta(kb_dir)
            existing_versions = existing_meta.get("versions") if existing_meta else None
            _write_kb_meta(
                kb_dir, pack_id, manifest_pack.get("db_type"), target_version, subdirectory,
                existing_versions,
            )

            doc_count = len(md_files)
            logger.info(
                "pack_version_switched %s",
                fmt_kv(pack_id=pack_id, kb_id=kb.id, version=target_version, doc_count=doc_count),
            )
            return {"pack_id": pack_id, "version": target_version, "doc_count": doc_count}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    async def uninstall(self, pack_id: str, db_session_factory: Any) -> None:
        from app.models import models

        db = db_session_factory()
        try:
            kb = (
                db.query(models.KnowledgeBase)
                .filter(models.KnowledgeBase.pack_id == pack_id, models.KnowledgeBase.source == "pack")
                .first()
            )
            if not kb:
                raise ValueError(f"Pack '{pack_id}' is not installed")

            kb_dir = self._data_root / str(kb.id)
            db.delete(kb)
            db.commit()

            if kb_dir.exists():
                shutil.rmtree(kb_dir, ignore_errors=True)

            progress.clear(pack_id)
            logger.info("pack_uninstall_complete %s", fmt_kv(pack_id=pack_id))
        finally:
            db.close()
