from pathlib import Path

from app.skills.store import SkillStore, SkillValidationError


def test_skill_store_crud_flow(tmp_path: Path):
    store = SkillStore(skills_dir=str(tmp_path))
    created = store.create(
        name="ob-slow-query",
        version="1.0.0",
        description="diagnose slow sql for oceanbase",
        database="oceanbase",
        always_apply=False,
        prompt="You are a slow query expert.",
    )
    assert created.name == "ob-slow-query"
    assert created.path.endswith("/custom/oceanbase/ob-slow-query.md")
    assert created.source == "custom"

    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].name == "ob-slow-query"

    updated = store.update(
        original_name="ob-slow-query",
        description="diagnose slow sql and execution plans",
        prompt="Updated prompt",
    )
    assert updated.description == "diagnose slow sql and execution plans"
    assert updated.prompt == "Updated prompt"
    assert updated.source == "custom"

    searched = store.search("execution")
    assert len(searched) == 1
    assert searched[0].name == "ob-slow-query"

    store.delete("ob-slow-query")
    assert store.load() == []


def test_skill_store_update_moves_file_by_database(tmp_path: Path):
    store = SkillStore(skills_dir=str(tmp_path))
    created = store.create(
        name="generic-sql-helper",
        version="1.0.0",
        description="generic sql helper for triage tasks",
        database="mysql",
        always_apply=False,
        prompt="You are a SQL helper.",
    )
    assert created.path.endswith("/custom/mysql/generic-sql-helper.md")

    updated = store.update(
        original_name="generic-sql-helper",
        database="oceanbase",
        always_apply=True,
    )
    assert updated.path.endswith("/custom/oceanbase/generic-sql-helper.md")
    assert updated.always_apply is True


def test_skill_store_rejects_invalid_front_matter(tmp_path: Path):
    bad_file = tmp_path / "bad.md"
    bad_file.write_text("invalid markdown content", encoding="utf-8")

    store = SkillStore(skills_dir=str(tmp_path))
    loaded = store.load()
    assert loaded == []
    assert store.errors


def test_skill_store_ignores_readme_markdown(tmp_path: Path):
    (tmp_path / "README.md").write_text("# index", encoding="utf-8")
    (tmp_path / "oceanbase").mkdir()
    (tmp_path / "oceanbase" / "README.md").write_text("# db index", encoding="utf-8")

    store = SkillStore(skills_dir=str(tmp_path))
    loaded = store.load()
    assert loaded == []
    assert store.errors == []


def test_skill_store_rejects_duplicate_create(tmp_path: Path):
    store = SkillStore(skills_dir=str(tmp_path))
    store.create(
        name="ob-lock-analysis",
        version="1.0.0",
        description="analyze lock waits for oceanbase",
        database="oceanbase",
        always_apply=False,
        prompt="lock analysis",
    )
    try:
        store.create(
            name="ob-lock-analysis",
            version="1.0.0",
            description="analyze lock waits for oceanbase",
            database="oceanbase",
            always_apply=False,
            prompt="lock analysis",
        )
        assert False, "Expected SkillValidationError"
    except SkillValidationError:
        assert True


def test_skill_store_supports_non_ascii_name(tmp_path: Path):
    store = SkillStore(skills_dir=str(tmp_path))
    created = store.create(
        name="慢查询诊断",
        version="1.0.0",
        description="用于诊断慢查询并输出优化建议",
        database="oceanbase",
        always_apply=False,
        prompt="你是慢查询诊断专家。",
    )
    assert created.name == "慢查询诊断"
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].name == "慢查询诊断"


def test_skill_store_requires_always_apply_in_frontmatter(tmp_path: Path):
    bad = tmp_path / "oceanbase" / "bad.md"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        """---
name: xx
version: 1.0.0
description: this is a valid description
database: oceanbase
---
prompt
""",
        encoding="utf-8",
    )
    store = SkillStore(skills_dir=str(tmp_path))
    loaded = store.load()
    assert loaded == []
    assert store.errors


def test_skill_store_infers_built_in_by_path_and_blocks_mutation(tmp_path: Path):
    built_in = tmp_path / "oceanbase" / "builtin.md"
    built_in.parent.mkdir(parents=True)
    built_in.write_text(
        """---
name: ob-built-in
version: 1.0.0
description: built in skill for system use
database: oceanbase
always_apply: false
---
built in prompt
""",
        encoding="utf-8",
    )

    store = SkillStore(skills_dir=str(tmp_path))
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].source == "built_in"

    try:
        store.update("ob-built-in", description="changed")
        assert False, "Expected SkillValidationError"
    except SkillValidationError as exc:
        assert "read-only" in str(exc)

    try:
        store.delete("ob-built-in")
        assert False, "Expected SkillValidationError"
    except SkillValidationError as exc:
        assert "read-only" in str(exc)
