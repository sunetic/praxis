from __future__ import annotations

from pathlib import Path

import pytest

from app.services.knowledge.search_tools import (
    discover,
    execute_tool,
    outline,
    read,
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
