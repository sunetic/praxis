from pathlib import Path

from app.skills.store import SkillStore

BUILTIN_DIR = str(Path(__file__).resolve().parents[1] / "data" / "skills")

EXPECTED_SKILLS = {
    "database-claim-provenance": ("general", False, "built_in"),
    "mysql-connection-diagnosis": ("mysql", False, "built_in"),
    "mysql-innodb-health": ("mysql", False, "built_in"),
    "mysql-lock-diagnosis": ("mysql", False, "built_in"),
    "mysql-replication-check": ("mysql", False, "built_in"),
    "mysql-slow-query-triage": ("mysql", False, "built_in"),
    "pg-connection-diagnosis": ("postgresql", False, "built_in"),
    "pg-lock-diagnosis": ("postgresql", False, "built_in"),
    "pg-replication-check": ("postgresql", False, "built_in"),
    "pg-slow-query-triage": ("postgresql", False, "built_in"),
    "pg-vacuum-health": ("postgresql", False, "built_in"),
    "skill-layered-diagnosis-policy": ("general", True, "built_in"),
}


def _load_builtin():
    store = SkillStore(skills_dir=BUILTIN_DIR)
    skills = store.load()
    return store, skills


def test_all_builtin_skills_parse_successfully():
    store, skills = _load_builtin()
    assert store.errors == [], f"Parse errors: {store.errors}"
    assert len(skills) == len(EXPECTED_SKILLS)


def test_builtin_skill_metadata_snapshot():
    _, skills = _load_builtin()
    actual = {s.name: (s.database, s.always_apply, s.source) for s in skills}
    assert actual == EXPECTED_SKILLS


def test_only_policy_is_always_apply():
    _, skills = _load_builtin()
    always_on = [s.name for s in skills if s.always_apply]
    assert always_on == ["skill-layered-diagnosis-policy"]


def test_mysql_skills_have_mysql_database():
    _, skills = _load_builtin()
    for s in skills:
        if s.name.startswith("mysql-"):
            assert s.database == "mysql", f"{s.name} has database={s.database}"


def test_pg_skills_have_postgresql_database():
    _, skills = _load_builtin()
    for s in skills:
        if s.name.startswith("pg-"):
            assert s.database == "postgresql", f"{s.name} has database={s.database}"


def test_no_empty_prompts():
    _, skills = _load_builtin()
    for s in skills:
        assert len(s.prompt) >= 50, f"{s.name} prompt too short ({len(s.prompt)} chars)"


def test_no_duplicate_names():
    _, skills = _load_builtin()
    names = [s.name for s in skills]
    assert len(names) == len(set(names)), f"Duplicates: {[n for n in names if names.count(n) > 1]}"


def test_description_min_length():
    _, skills = _load_builtin()
    for s in skills:
        assert len(s.description) >= 20, f"{s.name} description too short: {s.description!r}"


def test_database_claim_provenance_exposes_a_verifier_policy_extension():
    store, _ = _load_builtin()
    skill = store.get("database-claim-provenance")

    assert skill is not None
    assert skill.always_apply is False
    assert "<completion_verification_policy>" in skill.rules_prompt
    assert "actual query request and returned evidence" in skill.rules_prompt


def test_all_versions_are_semver():
    _, skills = _load_builtin()
    import re

    for s in skills:
        assert re.match(r"^\d+\.\d+\.\d+$", s.version), f"{s.name} version={s.version!r}"
