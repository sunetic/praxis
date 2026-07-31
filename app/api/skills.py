from fastapi import APIRouter, HTTPException, Query, status

from app.core.logging import fmt_kv, get_logger
from app.schemas import schemas
from app.skills.store import Skill, SkillValidationError, skill_store

router = APIRouter(prefix="/skills", tags=["Skills"])
logger = get_logger("api.skills")
BUILT_IN_PROMPT_PLACEHOLDER = "[built-in skill prompt hidden]"


def _to_skill_response(skill: Skill) -> schemas.SkillResponse:
    include_prompt = skill.source == "custom"
    payload = skill.to_dict(include_prompt=include_prompt)
    if skill.source != "custom":
        payload["prompt"] = BUILT_IN_PROMPT_PLACEHOLDER
    if skill.source != "custom":
        payload["path"] = ""
    return schemas.SkillResponse(**payload)


@router.get("", response_model=list[schemas.SkillResponse])
def list_skills(query: str | None = Query(default=None, max_length=100)):
    skill_store.load()
    records = skill_store.search(query=query)
    logger.info("list_skills %s", fmt_kv(query=query, count=len(records)))
    return [_to_skill_response(item) for item in records]


@router.get("/{skill_name}", response_model=schemas.SkillResponse)
def get_skill(skill_name: str):
    skill_store.load()
    skill = skill_store.get(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return _to_skill_response(skill)


@router.post("", response_model=schemas.SkillResponse, status_code=status.HTTP_201_CREATED)
def create_skill(payload: schemas.SkillCreate):
    try:
        created = skill_store.create(
            name=payload.name,
            version=payload.version,
            description=payload.description,
            database=payload.database,
            always_apply=payload.always_apply,
            prompt=payload.prompt,
        )
    except SkillValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    logger.info("create_skill %s", fmt_kv(name=created.name))
    return _to_skill_response(created)


@router.patch("/{skill_name}", response_model=schemas.SkillResponse)
def update_skill(skill_name: str, payload: schemas.SkillUpdate):
    try:
        updated = skill_store.update(
            original_name=skill_name,
            name=payload.name,
            version=payload.version,
            description=payload.description,
            database=payload.database,
            always_apply=payload.always_apply,
            prompt=payload.prompt,
        )
    except SkillValidationError as e:
        detail = str(e)
        if detail.endswith("not found"):
            code = 404
        elif "read-only" in detail:
            code = 403
        else:
            code = 400
        raise HTTPException(status_code=code, detail=detail) from e
    logger.info("update_skill %s", fmt_kv(name=updated.name))
    return _to_skill_response(updated)


@router.delete("/{skill_name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(skill_name: str):
    try:
        skill_store.delete(skill_name)
    except SkillValidationError as e:
        detail = str(e)
        if "read-only" in detail:
            raise HTTPException(status_code=403, detail=detail) from e
        raise HTTPException(status_code=404, detail=detail) from e
    logger.info("delete_skill %s", fmt_kv(name=skill_name))
    return None
