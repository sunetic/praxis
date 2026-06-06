import shutil
from pathlib import Path

import pytest

from app.services.platform.skill_selector import select_skills_for_context
from app.skills.store import SkillStore

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "data" / "skills"

MYSQL_SKILLS = {
    "mysql-connection-diagnosis",
    "mysql-innodb-health",
    "mysql-lock-diagnosis",
    "mysql-replication-check",
    "mysql-slow-query-triage",
}
PG_SKILLS = {
    "pg-connection-diagnosis",
    "pg-lock-diagnosis",
    "pg-replication-check",
    "pg-slow-query-triage",
    "pg-vacuum-health",
}
ALL_DB_SKILLS = MYSQL_SKILLS | PG_SKILLS


@pytest.fixture()
def skill_store(tmp_path: Path) -> SkillStore:
    target = tmp_path / "skills"
    shutil.copytree(BUILTIN_DIR, target)
    return SkillStore(skills_dir=str(target))


async def _select(store: SkillStore, prompt: str) -> set[str]:
    result = await select_skills_for_context(
        prompt=prompt,
        skill_store_instance=store,
    )
    assert result["selector_ok"], f"Selector failed: {result['reason']}"
    return set(result["active_skills"])


@pytest.mark.llm
@pytest.mark.anyio
async def test_mysql_slow_query_selects_mysql_skills(skill_store):
    active = await _select(skill_store, "MySQL 慢查询特别多，帮我排查一下")
    assert "mysql-slow-query-triage" in active
    assert active.isdisjoint(PG_SKILLS), f"PG skills leaked: {active & PG_SKILLS}"


@pytest.mark.llm
@pytest.mark.anyio
async def test_pg_lock_selects_pg_skills(skill_store):
    active = await _select(skill_store, "PostgreSQL 锁等待很严重，很多事务被阻塞")
    assert "pg-lock-diagnosis" in active
    assert active.isdisjoint(MYSQL_SKILLS), f"MySQL skills leaked: {active & MYSQL_SKILLS}"


@pytest.mark.llm
@pytest.mark.anyio
async def test_pg_vacuum_selects_vacuum_skill(skill_store):
    active = await _select(skill_store, "PG 表膨胀严重，autovacuum 好像没跑起来")
    assert "pg-vacuum-health" in active
    assert active.isdisjoint(MYSQL_SKILLS), f"MySQL skills leaked: {active & MYSQL_SKILLS}"


@pytest.mark.llm
@pytest.mark.anyio
async def test_mysql_replication_selects_repl_skill(skill_store):
    active = await _select(skill_store, "MySQL 主从延迟越来越大，需要排查原因")
    assert "mysql-replication-check" in active
    assert active.isdisjoint(PG_SKILLS), f"PG skills leaked: {active & PG_SKILLS}"


@pytest.mark.llm
@pytest.mark.anyio
async def test_irrelevant_prompt_selects_nothing(skill_store):
    active = await _select(skill_store, "帮我写一个 React 登录页面，用 TypeScript")
    assert active.isdisjoint(ALL_DB_SKILLS), f"DB skills on irrelevant prompt: {active & ALL_DB_SKILLS}"


@pytest.mark.llm
@pytest.mark.anyio
async def test_always_apply_policy_always_present(skill_store):
    active = await _select(skill_store, "随便聊聊天气怎么样")
    assert "skill-layered-diagnosis-policy" in active


@pytest.mark.llm
@pytest.mark.anyio
async def test_cross_engine_no_contamination(skill_store):
    cases = [
        ("MySQL 连接数快满了", MYSQL_SKILLS, PG_SKILLS),
        ("MySQL InnoDB 的 buffer pool 命中率很低", MYSQL_SKILLS, PG_SKILLS),
        ("MySQL 出现了死锁", MYSQL_SKILLS, PG_SKILLS),
        ("PostgreSQL 慢 SQL 需要优化", PG_SKILLS, MYSQL_SKILLS),
        ("PG 流复制延迟监控", PG_SKILLS, MYSQL_SKILLS),
        ("PostgreSQL 连接数异常", PG_SKILLS, MYSQL_SKILLS),
    ]
    for prompt, _own_pool, wrong_pool in cases:
        active = await _select(skill_store, prompt)
        leaked = active & wrong_pool
        assert not leaked, f"Cross-engine leak for '{prompt}': {leaked}"
