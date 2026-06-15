from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger

logger = get_logger("knowledge.pack_installer")

_CLONE_TIMEOUT = 300
_CHECKOUT_TIMEOUT = 60


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


class PackInstaller:
    def __init__(self) -> None:
        settings = get_settings()
        self._data_root = Path(settings.data_dir if hasattr(settings, "data_dir") else "data") / "knowledge"

    async def install(self, pack: dict[str, Any], db_session_factory: Any) -> int:
        pack_id = pack["id"]
        progress.set(pack_id, "downloading", progress_message="Checking git availability")

        if not await _check_git_available():
            progress.set(pack_id, "error", error_message="git is not installed on the server")
            raise RuntimeError("git is not installed on the server")

        tmp_dir = tempfile.mkdtemp(prefix=f"kb_pack_{pack_id}_")
        try:
            progress.set(pack_id, "downloading", progress_message="Cloning repository")

            rc, _, err = await _run_git(
                ["clone", "--filter=blob:none", "--no-checkout", "--depth", "1",
                 "--branch", pack["branch"], pack["repo_url"], tmp_dir],
                cwd=tmp_dir,
                timeout=_CLONE_TIMEOUT,
            )
            if rc != 0:
                raise RuntimeError(f"git clone failed: {err[:500]}")

            progress.set(pack_id, "downloading", progress_message="Setting up sparse checkout")

            rc, _, err = await _run_git(
                ["sparse-checkout", "init", "--cone"],
                cwd=tmp_dir,
            )
            if rc != 0:
                raise RuntimeError(f"sparse-checkout init failed: {err[:500]}")

            rc, _, err = await _run_git(
                ["sparse-checkout", "set", pack["subdirectory"]],
                cwd=tmp_dir,
            )
            if rc != 0:
                raise RuntimeError(f"sparse-checkout set failed: {err[:500]}")

            rc, _, err = await _run_git(
                ["checkout"],
                cwd=tmp_dir,
                timeout=_CLONE_TIMEOUT,
            )
            if rc != 0:
                raise RuntimeError(f"git checkout failed: {err[:500]}")

            progress.set(pack_id, "downloading", progress_message="Scanning documents")

            src_dir = Path(tmp_dir) / pack["subdirectory"]
            if not src_dir.is_dir():
                raise RuntimeError(f"Subdirectory not found after checkout: {pack['subdirectory']}")

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

            from app.models import models

            db = db_session_factory()
            try:
                kb = models.KnowledgeBase(
                    name=pack["name"],
                    description=pack["description"],
                    tags=pack.get("tags"),
                    source="pack",
                    pack_id=pack_id,
                )
                db.add(kb)
                db.commit()
                db.refresh(kb)

                kb_dir = self._data_root / str(kb.id)
                kb_dir.mkdir(parents=True, exist_ok=True)

                for md_path in md_files:
                    rel = md_path.relative_to(src_dir)
                    dest = kb_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(md_path, dest)

                    doc = models.KnowledgeDocument(
                        kb_id=kb.id,
                        title=md_path.stem.replace("-", " ").replace("_", " "),
                        filename=str(rel),
                        content_path=str(dest),
                        size_bytes=dest.stat().st_size,
                    )
                    db.add(doc)

                db.commit()
                logger.info(
                    "pack_install_complete %s",
                    fmt_kv(pack_id=pack_id, kb_id=kb.id, doc_count=len(md_files)),
                )
                progress.set(pack_id, "installed", kb_id=kb.id)
                return kb.id
            except Exception:
                db.rollback()
                if kb_dir.exists():
                    shutil.rmtree(kb_dir, ignore_errors=True)
                raise
            finally:
                db.close()
        except Exception as e:
            error_msg = str(e)[:500]
            if progress.get(pack_id) and progress.get(pack_id).get("status") != "installed":
                progress.set(pack_id, "error", error_message=error_msg)
            logger.exception("pack_install_failed %s", fmt_kv(pack_id=pack_id))
            raise
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

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
