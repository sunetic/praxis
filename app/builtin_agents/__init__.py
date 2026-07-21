"""Boot-time registration of built-in agents.

On startup, each module in ``app/builtin_agents/`` that exposes an
``AGENT_META`` dict is upserted into the ``agents`` table (matched by
``name``).  Existing agents with the same name are updated in-place so
their prompt and tool/skill lists stay in sync with the codebase.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.logging import fmt_kv, get_logger
from app.models.models import Agent

logger = get_logger("builtin_agents")

_PACKAGE_DIR = Path(__file__).parent


def register_builtin_agents(db: Session) -> list[str]:
    """Scan this package for builtin agent modules and upsert them."""
    registered: list[str] = []
    for _finder, module_name, _is_pkg in pkgutil.iter_modules([str(_PACKAGE_DIR)]):
        if module_name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"app.builtin_agents.{module_name}")
        except Exception:
            logger.warning("builtin_agent_import_failed module=%s", module_name)
            continue

        meta = getattr(mod, "AGENT_META", None)
        if not meta:
            continue

        name = meta["name"]
        existing = db.query(Agent).filter(Agent.name == name).first()
        if existing:
            existing.description = meta.get("description", existing.description)
            existing.prompt = meta["prompt"]
            existing.tools = meta.get("tools", existing.tools)
            existing.skills = meta.get("skills", existing.skills)
            existing.agent_type = meta.get("agent_type", "builtin")
            logger.info("builtin_agent_updated %s", fmt_kv(name=name))
        else:
            agent = Agent(
                name=name,
                description=meta.get("description"),
                prompt=meta["prompt"],
                tools=meta.get("tools", []),
                skills=meta.get("skills", []),
                agent_type=meta.get("agent_type", "builtin"),
                status="active",
            )
            db.add(agent)
            logger.info("builtin_agent_created %s", fmt_kv(name=name))

        registered.append(name)

    if registered:
        db.commit()
    return registered
