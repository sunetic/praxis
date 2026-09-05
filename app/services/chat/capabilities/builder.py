from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import models
from app.services.integration.bindings import list_bound_services as resolve_bound_services
from app.skills.store import Skill

from .collectors import build_capability_context
from .contract import CapabilityBuildInput, CapabilityContext
from .renderer import render_capability_context


def build_prompt_capability_context(payload: CapabilityBuildInput) -> tuple[CapabilityContext, str]:
    context = build_capability_context(payload)
    return context, render_capability_context(context)


def list_active_skill_models(skill_names: list[str], loaded_skills: list[Skill]) -> list[Skill]:
    selected = set(skill_names)
    return [skill for skill in loaded_skills if skill.name in selected]


def list_bound_services(db: Session, datasource: models.DataSource | None) -> list[models.Service]:
    return resolve_bound_services(db, datasource)


def list_knowledge_bases(db: Session) -> list[models.KnowledgeBase]:
    return db.query(models.KnowledgeBase).order_by(models.KnowledgeBase.id.asc()).all()
