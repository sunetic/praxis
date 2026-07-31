"""Boot-time registration of built-in functions.

On startup, each module in ``app/builtin_functions/`` that exposes a
``FUNCTION_META`` dict and a ``main`` callable is upserted into the
``functions`` table (matched by ``slug``).  The source file contents
become the ``draft_code`` so the function can be invoked through the
normal runtime.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.logging import fmt_kv, get_logger
from app.models.models import Function, FunctionRelease

logger = get_logger("builtin_functions")

_PACKAGE_DIR = Path(__file__).parent


def register_builtin_functions(db: Session) -> list[str]:
    """Scan this package for builtin function modules and upsert them."""
    registered: list[str] = []
    for finder, module_name, _is_pkg in pkgutil.iter_modules([str(_PACKAGE_DIR)]):
        if module_name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"app.builtin_functions.{module_name}")
        except Exception:
            logger.warning("builtin_function_import_failed module=%s", module_name)
            continue

        meta = getattr(mod, "FUNCTION_META", None)
        main_fn = getattr(mod, "main", None)
        if not meta or not callable(main_fn):
            continue

        slug = meta["slug"]
        name = meta["name"]
        kind = meta.get("kind", "builtin")
        deps = meta.get("dependencies", {})

        source_path = _PACKAGE_DIR / f"{module_name}.py"
        code = source_path.read_text(encoding="utf-8")

        existing = db.query(Function).filter(Function.slug == slug).first()
        if existing:
            fn = existing
            fn.name = name
            fn.kind = kind
            fn.status = "released"
            fn.draft_code = code
            fn.draft_dependencies = deps
            fn.source_path = str(source_path)
            logger.info("builtin_function_updated %s", fmt_kv(slug=slug))
        else:
            fn = Function(
                name=name,
                slug=slug,
                kind=kind,
                status="released",
                draft_code=code,
                draft_dependencies=deps,
                source_path=str(source_path),
            )
            db.add(fn)
            db.flush()
            logger.info("builtin_function_created %s", fmt_kv(slug=slug))

        current_release = fn.current_release
        release_needs_sync = (
            current_release is None
            or current_release.code_snapshot != code
            or (current_release.dependency_manifest or {}) != deps
        )
        if release_needs_sync:
            latest_version = max((release.version for release in fn.releases), default=0)
            release = FunctionRelease(
                function=fn,
                function_id=fn.id,
                version=latest_version + 1,
                code_snapshot=code,
                dependency_manifest=deps,
            )
            fn.releases.append(release)
            fn.current_release = release
            logger.info(
                "builtin_function_release_synced %s", fmt_kv(slug=slug, version=release.version)
            )

        registered.append(slug)

    if registered:
        db.commit()
    return registered
