from pathlib import Path

from fastapi import HTTPException
import pytest

from app.api import skills as skills_api
from app.schemas import schemas
from app.skills.store import SkillStore


def _seed_skill_store(tmp_path: Path) -> SkillStore:
    built_in = tmp_path / "oceanbase" / "ob-built-in.md"
    built_in.parent.mkdir(parents=True)
    built_in.write_text(
        """---
name: ob-built-in
version: 1.0.0
description: built in skill for internal runtime only
database: oceanbase
always_apply: false
---
built in secret prompt
""",
        encoding="utf-8",
    )
    store = SkillStore(skills_dir=str(tmp_path))
    store.create(
        name="ob-custom",
        version="1.0.0",
        description="custom skill managed by user",
        database="oceanbase",
        always_apply=False,
        prompt="custom prompt",
    )
    return store


def test_skills_api_redacts_built_in_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    store = _seed_skill_store(tmp_path)
    monkeypatch.setattr(skills_api, "skill_store", store)

    records = skills_api.list_skills(query=None)
    by_name = {item.name: item for item in records}

    assert by_name["ob-built-in"].source == "built_in"
    assert by_name["ob-built-in"].prompt == "[built-in skill prompt hidden]"
    assert by_name["ob-built-in"].path == ""

    assert by_name["ob-custom"].source == "custom"
    assert by_name["ob-custom"].prompt == "custom prompt"
    assert by_name["ob-custom"].path.endswith("/custom/oceanbase/ob-custom.md")

    built_in = skills_api.get_skill("ob-built-in")
    assert built_in.source == "built_in"
    assert built_in.prompt == "[built-in skill prompt hidden]"


def test_skills_api_blocks_builtin_update_and_delete(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    store = _seed_skill_store(tmp_path)
    monkeypatch.setattr(skills_api, "skill_store", store)

    try:
        skills_api.update_skill("ob-built-in", schemas.SkillUpdate(description="new desc"))
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 403

    try:
        skills_api.delete_skill("ob-built-in")
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 403


def test_skills_api_create_returns_custom_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    store = _seed_skill_store(tmp_path)
    monkeypatch.setattr(skills_api, "skill_store", store)

    created = skills_api.create_skill(
        schemas.SkillCreate(
            name="ob-custom-2",
            version="1.0.0",
            description="second custom skill for tests",
            database="oceanbase",
            always_apply=False,
            prompt="custom-2 prompt",
        )
    )

    assert created.source == "custom"
    assert created.prompt == "custom-2 prompt"
    assert created.path.endswith("/custom/oceanbase/ob-custom-2.md")
