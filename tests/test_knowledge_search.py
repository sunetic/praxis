from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app.services.knowledge.query_expansion import build_query_plan
from app.services.knowledge.search_tools import (
    KnowledgeToolExecutor,
    SearchTarget,
    discover,
    execute_tool,
    outline,
    read,
    resolve_search_targets,
    search,
)


@pytest.fixture()
def kb_dir(tmp_path: Path) -> Path:
    kb_root = tmp_path / "data" / "knowledge" / "1"
    kb_root.mkdir(parents=True)

    (kb_root / "tutorial").mkdir()
    tutorial_file = kb_root / "tutorial" / "postgresql-select.md"
    tutorial_file.write_text(
        "---\ntitle: PostgreSQL SELECT\n---\n\n"
        "## Introduction\n\n"
        "The SELECT statement is used to query data from tables.\n\n"
        "## Syntax\n\n"
        "```sql\nSELECT column1, column2 FROM table_name;\n```\n\n"
        "## Examples\n\n"
        "### Basic SELECT\n\n"
        "```sql\nSELECT * FROM users;\n```\n",
        encoding="utf-8",
    )

    (kb_root / "window-function").mkdir()
    rank_file = kb_root / "window-function" / "rank-function.md"
    rank_file.write_text(
        "---\ntitle: PostgreSQL RANK Function\n---\n\n"
        "## Introduction to RANK()\n\n"
        "The RANK() function assigns a rank to every row within a partition.\n\n"
        "## Syntax\n\n"
        "```sql\nRANK() OVER (PARTITION BY col ORDER BY col)\n```\n\n"
        "## Example\n\n"
        "```sql\nSELECT name, RANK() OVER (ORDER BY score DESC) FROM students;\n```\n",
        encoding="utf-8",
    )

    dense_rank_file = kb_root / "window-function" / "dense-rank-function.md"
    dense_rank_file.write_text(
        "---\ntitle: PostgreSQL DENSE_RANK Function\n---\n\n"
        "## Introduction\n\n"
        "DENSE_RANK assigns consecutive ranks without gaps.\n",
        encoding="utf-8",
    )

    index_file = kb_root / "indexes.md"
    index_file.write_text(
        "---\ntitle: PostgreSQL Indexes\n---\n\n"
        "## Types of Indexes\n\n"
        "PostgreSQL supports B-tree, Hash, GiST, GIN indexes.\n\n"
        "## When to Use Indexes\n\n"
        "Use indexes on columns used in WHERE, JOIN, ORDER BY.\n\n"
        "## Index Best Practices\n\n"
        "Avoid over-indexing. Monitor pg_stat_user_indexes.\n",
        encoding="utf-8",
    )

    return kb_root


@pytest.fixture(autouse=True)
def _patch_data_root(kb_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.knowledge.search_tools._DATA_ROOT",
        kb_dir.parent,
    )


class TestDiscover:
    def test_find_by_filename(self, kb_dir: Path) -> None:
        results = discover(1, "rank")
        names = [r["path"] for r in results]
        assert any("rank-function.md" in n for n in names)
        assert any("dense-rank-function.md" in n for n in names)

    def test_find_by_title(self, kb_dir: Path) -> None:
        results = discover(1, "SELECT")
        assert any("postgresql-select.md" in r["path"] for r in results)

    def test_find_by_directory(self, kb_dir: Path) -> None:
        results = discover(1, "window")
        assert len(results) >= 2

    def test_no_match(self, kb_dir: Path) -> None:
        results = discover(1, "nonexistent_xyz_12345")
        assert results == []

    def test_max_results(self, kb_dir: Path) -> None:
        results = discover(1, "function", max_results=1)
        assert len(results) == 1

    def test_kb_not_found(self, kb_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            discover(999, "test")


class TestSearch:
    def test_basic_search(self, kb_dir: Path) -> None:
        results = search(1, "RANK")
        assert len(results) > 0
        assert any("rank" in r["file"].lower() for r in results)

    def test_search_with_paths(self, kb_dir: Path) -> None:
        results = search(1, "RANK", paths=["window-function/"])
        assert len(results) > 0
        assert all("window-function" in r["file"] for r in results)

    def test_search_with_context(self, kb_dir: Path) -> None:
        results = search(1, "RANK", context_lines=2)
        if results:
            assert "context" in results[0]

    def test_search_no_match(self, kb_dir: Path) -> None:
        results = search(1, "xyzzy_nonexistent_term_12345")
        assert results == []

    def test_search_case_insensitive(self, kb_dir: Path) -> None:
        results_lower = search(1, "rank")
        results_upper = search(1, "RANK")
        assert len(results_lower) == len(results_upper)


class TestRead:
    def test_read_full(self, kb_dir: Path) -> None:
        result = read(1, "indexes.md")
        assert "total_lines" in result
        assert "content" in result
        assert "B-tree" in result["content"]

    def test_read_line_range(self, kb_dir: Path) -> None:
        result = read(1, "indexes.md", start_line=1, end_line=5)
        lines = result["content"].split("\n")
        assert len(lines) <= 5

    def test_read_not_found(self, kb_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read(1, "nonexistent.md")

    def test_read_path_escape(self, kb_dir: Path) -> None:
        with pytest.raises(ValueError):
            read(1, "../../etc/passwd")


class TestOutline:
    def test_extract_headings(self, kb_dir: Path) -> None:
        headings = outline(1, "indexes.md")
        titles = [h["title"] for h in headings]
        assert "Types of Indexes" in titles
        assert "When to Use Indexes" in titles
        assert "Index Best Practices" in titles

    def test_heading_levels(self, kb_dir: Path) -> None:
        headings = outline(1, "tutorial/postgresql-select.md")
        levels = {h["title"]: h["level"] for h in headings}
        assert levels.get("Introduction") == 2
        assert levels.get("Basic SELECT") == 3

    def test_not_found(self, kb_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            outline(1, "nonexistent.md")


class TestExecuteTool:
    @pytest.mark.asyncio
    async def test_discover_via_executor(self, kb_dir: Path) -> None:
        result = await execute_tool("kb_discover", {"kb_id": 1, "query": "rank"})
        assert result["success"] is True
        assert isinstance(result["data"], list)

    @pytest.mark.asyncio
    async def test_read_via_executor(self, kb_dir: Path) -> None:
        result = await execute_tool("kb_read", {"kb_id": 1, "path": "indexes.md"})
        assert result["success"] is True
        assert "content" in result["data"]

    @pytest.mark.asyncio
    async def test_unknown_tool(self, kb_dir: Path) -> None:
        result = await execute_tool("kb_unknown", {"kb_id": 1})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_not_found_error(self, kb_dir: Path) -> None:
        result = await execute_tool("kb_read", {"kb_id": 1, "path": "nope.md"})
        assert result["success"] is False
        assert result["error"]["code"] == "not_found"


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture()
def versioned_git_kb(kb_dir: Path) -> dict[str, str | Path]:
    repo = kb_dir.parent / "2"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "kb-test@example.com")
    _run_git(repo, "config", "user.name", "Knowledge Test")

    docs = repo / "docs"
    docs.mkdir()
    reference = docs / "errors.md"
    reference.write_text(
        "---\ntitle: MySQL 8.0 Errors\n---\n\nVERSION_80_ONLY failed connection\n",
        encoding="utf-8",
    )
    _run_git(repo, "add", "docs/errors.md")
    _run_git(repo, "commit", "-q", "-m", "mysql 8.0 docs")
    commit_80 = _run_git(repo, "rev-parse", "HEAD")
    _run_git(repo, "branch", "8.0")
    _run_git(repo, "tag", "-a", "release-8.0", "-m", "MySQL 8.0", commit_80)

    reference.write_text(
        "---\ntitle: MySQL 8.4 Errors\n---\n\nVERSION_84_ONLY critical exception\n",
        encoding="utf-8",
    )
    _run_git(repo, "add", "docs/errors.md")
    _run_git(repo, "commit", "-q", "-m", "mysql 8.4 docs")
    commit_84 = _run_git(repo, "rev-parse", "HEAD")
    _run_git(repo, "branch", "8.4")

    meta = {
        "pack_id": "mysql-versioned-test",
        "db_type": "mysql",
        "version": "8.4",
        "subdirectory": "docs",
        "versions": [
            {
                "branch": "release-8.0",
                "label": "8.0",
                "ref": "refs/tags/release-8.0",
                "ref_type": "tag",
            },
            {
                "branch": "8.4",
                "label": "8.4",
                "ref": "refs/heads/8.4",
                "ref_type": "branch",
            },
        ],
    }
    (repo / ".kb_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return {
        "repo": repo,
        "commit_80": commit_80,
        "commit_84": commit_84,
    }


class TestVersionPinnedGitSearch:
    @pytest.mark.asyncio
    async def test_db_type_and_version_select_compatible_pack(
        self,
        versioned_git_kb: dict[str, str | Path],
    ) -> None:
        targets = await resolve_search_targets(
            kb_ids=None,
            db_type="mysql",
            version="8.0",
        )

        assert len(targets) == 1
        assert targets[0].kb_id == 2
        assert targets[0].commit_sha == versioned_git_kb["commit_80"]

    @pytest.mark.asyncio
    async def test_explicit_kb_id_and_version_pin_tag_commit(
        self,
        versioned_git_kb: dict[str, str | Path],
    ) -> None:
        repo = Path(versioned_git_kb["repo"])
        head_before = _run_git(repo, "rev-parse", "HEAD")

        target = (await resolve_search_targets(kb_ids=[2], db_type=None, version="8.0"))[0]
        results = search(2, "VERSION_80_ONLY|VERSION_84_ONLY", target=target)

        assert target.resolved_version == "8.0"
        assert target.commit_sha == versioned_git_kb["commit_80"]
        assert results
        assert all("VERSION_80_ONLY" in item["context"] for item in results)
        assert all("VERSION_84_ONLY" not in item["context"] for item in results)
        assert _run_git(repo, "rev-parse", "HEAD") == head_before

    @pytest.mark.asyncio
    async def test_concurrent_versions_never_cross_contaminate(
        self,
        versioned_git_kb: dict[str, str | Path],
    ) -> None:
        repo = Path(versioned_git_kb["repo"])
        head_before = _run_git(repo, "rev-parse", "HEAD")

        async def run_version(version: str) -> tuple[str, list[dict]]:
            target = (await resolve_search_targets(kb_ids=[2], db_type=None, version=version))[0]
            await asyncio.sleep(0)
            results = await asyncio.to_thread(
                search,
                2,
                "VERSION_80_ONLY|VERSION_84_ONLY",
                target=target,
            )
            return version, results

        completed = await asyncio.gather(
            *(run_version("8.0" if index % 2 == 0 else "8.4") for index in range(40))
        )

        for version, results in completed:
            expected = "VERSION_80_ONLY" if version == "8.0" else "VERSION_84_ONLY"
            rejected = "VERSION_84_ONLY" if version == "8.0" else "VERSION_80_ONLY"
            assert results
            assert all(expected in item["context"] for item in results)
            assert all(rejected not in item["context"] for item in results)
            assert all(item["version"] == version for item in results)
        assert _run_git(repo, "rev-parse", "HEAD") == head_before

    @pytest.mark.asyncio
    async def test_unknown_version_fails_closed(
        self,
        versioned_git_kb: dict[str, str | Path],
    ) -> None:
        with pytest.raises(ValueError, match="not available"):
            await resolve_search_targets(kb_ids=[2], db_type=None, version="9.9")


class TestQueryCoverage:
    def test_error_query_expands_exact_identifiers_and_bilingual_terms(self) -> None:
        plan = build_query_plan(
            '排查错误 "Deadlock found when trying to get lock" ER_LOCK_DEADLOCK SQLSTATE 40001'
        )

        assert any("Deadlock" in value for value in plan.groups["exact_phrases"])
        assert "ER_LOCK_DEADLOCK" in plan.groups["identifiers"]
        assert any("40001" in value for value in plan.groups["identifiers"])
        assert "错误" in plan.groups["semantic_variants"]
        assert "error" in plan.groups["semantic_variants"]
        assert "failed" in plan.groups["semantic_variants"]
        assert "critical" in plan.groups["semantic_variants"]

    @pytest.mark.asyncio
    async def test_executor_enforces_full_seed_coverage(self, kb_dir: Path) -> None:
        (kb_dir / "errors.md").write_text(
            "A critical operation failed with an exception.\n",
            encoding="utf-8",
        )
        target = (await resolve_search_targets(kb_ids=[1], db_type=None, version=None))[0]
        plan = build_query_plan("查询错误信息")
        executor = KnowledgeToolExecutor([target], plan)

        result = await executor.execute("kb_search", {"kb_id": 1, "query": "错误"})
        coverage = executor.coverage_report()

        assert result["success"] is True
        assert any("failed" in item["context"] for item in result["data"])
        assert coverage["coverage_complete"] is True
        assert coverage["uncovered_groups"] == {}
        assert "failed" in coverage["searched_patterns"]

    @pytest.mark.asyncio
    async def test_coverage_is_required_for_every_target(self, kb_dir: Path) -> None:
        plan = build_query_plan("index error")
        targets = [
            SearchTarget(kb_id=1, source_type="filesystem", root=kb_dir),
            SearchTarget(kb_id=2, source_type="filesystem", root=kb_dir),
        ]
        executor = KnowledgeToolExecutor(targets, plan)

        first = await executor.execute("kb_search", {"kb_id": 1, "query": "index"})
        partial_coverage = executor.coverage_report()
        second = await executor.execute("kb_search", {"kb_id": 2, "query": "index"})
        complete_coverage = executor.coverage_report()

        assert first["success"] is True
        assert second["success"] is True
        assert partial_coverage["coverage_complete"] is False
        assert partial_coverage["target_coverage"]["1"]["coverage_complete"] is True
        assert partial_coverage["target_coverage"]["2"]["coverage_complete"] is False
        assert complete_coverage["coverage_complete"] is True

    @pytest.mark.asyncio
    async def test_failed_search_does_not_count_as_coverage(self, kb_dir: Path) -> None:
        target = SearchTarget(kb_id=1, source_type="filesystem", root=kb_dir)
        executor = KnowledgeToolExecutor([target], build_query_plan("index error"))

        result = await executor.execute("kb_search", {"kb_id": 1, "query": "("})
        coverage = executor.coverage_report()

        assert result["success"] is False
        assert coverage["coverage_complete"] is False
        assert coverage["searched_patterns"] == []

    def test_search_rejects_path_escape(self, kb_dir: Path) -> None:
        with pytest.raises(ValueError, match="Invalid knowledge base path"):
            search(1, "root", paths=["../../etc"])
