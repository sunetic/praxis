"""Built-in knowledge packs — mount point for bundled knowledge bases."""

from __future__ import annotations

import importlib
import json
import pkgutil
from pathlib import Path
from typing import Any

from app.core.config import DEFAULT_DATA_DIR
from app.core.logging import get_logger

logger = get_logger("builtin_knowledge")

_HERE = Path(__file__).parent
_MANIFEST_PATH = DEFAULT_DATA_DIR / "knowledge_packs.json"


def register_builtin_knowledge_packs() -> list[str]:
    """Seed the knowledge pack manifest with all bundled pack definitions.

    Returns the list of pack IDs that were newly added.
    """
    packs = _discover_packs()
    if not packs:
        return []
    added = _seed_manifest(packs)
    if added:
        logger.info("builtin_knowledge_packs_seeded count=%d ids=%s", len(added), added)
    return added


def _discover_packs() -> list[dict[str, Any]]:
    packs = []
    for _finder, name, _is_pkg in pkgutil.iter_modules([str(_HERE)]):
        try:
            mod = importlib.import_module(f"app.builtin_knowledge.{name}")
            if hasattr(mod, "PACK_META"):
                packs.append(mod.PACK_META)
        except ImportError:
            pass
    return packs


def _seed_manifest(new_packs: list[dict[str, Any]]) -> list[str]:
    _MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if _MANIFEST_PATH.is_file():
        try:
            existing = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    builtin_ids = {p["id"] for p in new_packs if p.get("id")}
    # Always overwrite builtin entries so local_path stays current after upgrades
    kept = [p for p in existing if p.get("id") not in builtin_ids]
    new_ids = {p["id"] for p in existing if p.get("id") in builtin_ids}
    merged = kept + new_packs
    _MANIFEST_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    added = [p["id"] for p in new_packs if p.get("id") not in new_ids]
    return added
