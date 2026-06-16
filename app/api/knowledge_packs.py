from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger
from app.db.database import SessionLocal, get_db
from app.models import models
from app.schemas import schemas
from app.services.knowledge.pack_installer import PackInstaller, progress, read_kb_meta

router = APIRouter(prefix="/knowledge-packs", tags=["KnowledgePacks"])
logger = get_logger("api.knowledge_packs")

settings = get_settings()
_MANIFEST_PATH = Path(settings.data_dir if hasattr(settings, "data_dir") else "data") / "knowledge_packs.json"
_installer = PackInstaller()
_background_tasks: set[asyncio.Task] = set()


def _load_manifest() -> list[dict]:
    if not _MANIFEST_PATH.is_file():
        return []
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def _find_pack(pack_id: str) -> dict:
    for pack in _load_manifest():
        if pack.get("id") == pack_id:
            return pack
    raise HTTPException(status_code=404, detail=f"Knowledge pack '{pack_id}' not found in manifest")


@router.get("", response_model=List[schemas.KnowledgePackResponse])
def list_packs(db: Session = Depends(get_db)):
    manifest = _load_manifest()
    installed_kbs = (
        db.query(models.KnowledgeBase)
        .filter(models.KnowledgeBase.source == "pack")
        .all()
    )
    installed_map = {kb.pack_id: kb for kb in installed_kbs if kb.pack_id}

    results = []
    for pack in manifest:
        pack_id = pack["id"]
        entry = schemas.KnowledgePackResponse(**pack, status="available")

        prog = progress.get(pack_id)
        if prog:
            entry.status = prog["status"]
            entry.kb_id = prog.get("kb_id")
            entry.error_message = prog.get("error_message")
        elif pack_id in installed_map:
            entry.status = "installed"
            kb = installed_map[pack_id]
            entry.kb_id = kb.id
            entry.current_version = kb.version
            kb_meta = read_kb_meta(_installer._data_root / str(kb.id))
            if kb_meta and kb_meta.get("versions"):
                entry.versions = [
                    schemas.PackVersion(branch=v["branch"], label=v["label"])
                    for v in kb_meta["versions"]
                ]

        results.append(entry)

    logger.info("list_packs %s", fmt_kv(count=len(results)))
    return results


@router.post("/{pack_id}/install", response_model=schemas.KnowledgePackInstallStatus, status_code=status.HTTP_202_ACCEPTED)
async def install_pack(pack_id: str, db: Session = Depends(get_db)):
    pack = _find_pack(pack_id)

    existing = (
        db.query(models.KnowledgeBase)
        .filter(models.KnowledgeBase.pack_id == pack_id, models.KnowledgeBase.source == "pack")
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Pack '{pack_id}' is already installed (kb_id={existing.id})")

    current = progress.get(pack_id)
    if current and current["status"] == "downloading":
        raise HTTPException(status_code=409, detail=f"Pack '{pack_id}' is already being installed")

    async def _do_install():
        try:
            await _installer.install(pack, SessionLocal)
        except Exception:
            pass

    task = asyncio.create_task(_do_install())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    logger.info("install_pack_started %s", fmt_kv(pack_id=pack_id))
    return schemas.KnowledgePackInstallStatus(
        pack_id=pack_id,
        status="downloading",
        progress_message="Installation started",
    )


@router.get("/{pack_id}/status", response_model=schemas.KnowledgePackInstallStatus)
def get_pack_status(pack_id: str, db: Session = Depends(get_db)):
    _find_pack(pack_id)

    prog = progress.get(pack_id)
    if prog:
        return schemas.KnowledgePackInstallStatus(
            pack_id=pack_id,
            status=prog["status"],
            progress_message=prog.get("progress_message"),
            kb_id=prog.get("kb_id"),
            error_message=prog.get("error_message"),
        )

    existing = (
        db.query(models.KnowledgeBase)
        .filter(models.KnowledgeBase.pack_id == pack_id, models.KnowledgeBase.source == "pack")
        .first()
    )
    if existing:
        return schemas.KnowledgePackInstallStatus(
            pack_id=pack_id, status="installed", kb_id=existing.id,
        )

    return schemas.KnowledgePackInstallStatus(pack_id=pack_id, status="available")


@router.post("/{pack_id}/switch-version", response_model=schemas.SwitchVersionResponse)
async def switch_version(pack_id: str, body: schemas.SwitchVersionRequest):
    pack = _find_pack(pack_id)
    try:
        result = await _installer.switch_version(pack_id, body.version, pack, SessionLocal)
        return schemas.SwitchVersionResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.delete("/{pack_id}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_pack(pack_id: str, db: Session = Depends(get_db)):
    _find_pack(pack_id)

    existing = (
        db.query(models.KnowledgeBase)
        .filter(models.KnowledgeBase.pack_id == pack_id, models.KnowledgeBase.source == "pack")
        .first()
    )
    if not existing:
        raise HTTPException(status_code=404, detail=f"Pack '{pack_id}' is not installed")

    await _installer.uninstall(pack_id, SessionLocal)
    logger.info("uninstall_pack %s", fmt_kv(pack_id=pack_id))
    return None
