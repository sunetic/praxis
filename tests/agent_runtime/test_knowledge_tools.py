import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.exceptions import ToolFailed
from test_models import config
from test_runtime import Script, call, returns

from app.api.capabilities import router as capabilities_router
from app.db.base import Base
from app.models.models import Agent, KnowledgeBase
from app.services.agent.application import RuntimeApplication
from app.services.agent.definitions import RunDependencies
from app.services.agent.models import ModelFactory
from app.services.knowledge import search_tools
from app.services.knowledge.agent_tools import KNOWLEDGE_TOOLS, knowledge_tools


@pytest.fixture
def knowledge(store, tmp_path, monkeypatch):
    Base.metadata.create_all(store.sessions.kw["bind"])
    root = tmp_path / "knowledge"
    with store.sessions.begin() as db:
        db.add_all([KnowledgeBase(id=1, name="Runbook"), KnowledgeBase(id=2, name="Private")])
    for identifier in (1, 2):
        (root / str(identifier)).mkdir(parents=True)
    (root / "1" / "retry.md").write_text(
        "# Retry policy\nBudget is 3 attempts.\nWait 7 seconds.\n", encoding="utf-8"
    )
    (root / "2" / "secret.md").write_text("Not in this run's scope", encoding="utf-8")
    monkeypatch.setattr(search_tools, "_DATA_ROOT", root)
    return root, knowledge_tools(store.sessions)


def context(name, *, ids=(1,), actor="local", agent_id=None):
    return SimpleNamespace(
        tool_name=name,
        deps=RunDependencies(
            run_id="test-run",
            conversation_id="test-conversation",
            actor_id=actor,
            scope={"knowledge_base_ids": list(ids), "agent_id": agent_id},
        ),
    )


async def test_typed_tools_report_real_content_pagination_and_no_query_expansion(knowledge):
    root, tools = knowledge
    assert set(tools) == KNOWLEDGE_TOOLS
    assert all(not entry.mutating for entry in tools.values())
    listing = await tools["knowledge_list"].tool.function(context("knowledge_list"))
    assert [item["id"] for item in listing["knowledge_bases"]] == [1]
    search = tools["knowledge_search"].tool.function
    # The Chinese word is not silently expanded to match English retry text.
    assert (await search(context("knowledge_search"), 1, "错误"))["matches"] == []
    found = await search(context("knowledge_search"), 1, "Budget|Wait", limit=1)
    assert found["truncated"] is True
    assert found["pattern"] == "Budget|Wait"
    assert found["matches"][0]["file"] == "retry.md"
    first = await tools["knowledge_read"].tool.function(
        context("knowledge_read"), 1, "retry.md", limit=2
    )
    assert first["truncated"] and first["next_line"] == 3
    second = await tools["knowledge_read"].tool.function(
        context("knowledge_read"),
        1,
        "retry.md",
        start_line=3,
        expected_content_hash=first["content_hash"],
    )
    assert second["content"] == "3: Wait 7 seconds."
    assert second["next_line"] is None
    (root / "1" / "retry.md").write_text("Changed document", encoding="utf-8")
    with pytest.raises(ToolFailed, match="content changed"):
        await tools["knowledge_read"].tool.function(
            context("knowledge_read"), 1, "retry.md", expected_content_hash=first["content_hash"]
        )


async def test_discover_finds_uploaded_markdown_heading_without_matching_body(knowledge):
    root, tools = knowledge
    (root / "1" / "opaque-name.md").write_text(
        "# 队列容量配置\n\nBODY_ONLY_VALUE is 17.\n", encoding="utf-8"
    )
    discover = tools["knowledge_discover"].tool.function
    result = await discover(context("knowledge_discover"), 1, "队列容量")
    assert [(item["path"], item["title"]) for item in result["matches"]] == [
        ("opaque-name.md", "队列容量配置")
    ]
    assert (await discover(context("knowledge_discover"), 1, "BODY_ONLY_VALUE"))["matches"] == []
    # A metadata miss does not mean a full-text search found no evidence.
    found = await tools["knowledge_search"].tool.function(
        context("knowledge_search"), 1, "BODY_ONLY_VALUE"
    )
    assert found["matches"][0]["file"] == "opaque-name.md"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("# Heading only\nBody", "Heading only"),
        ("---\ntitle: 'Metadata title'\n---\n# Heading", "Metadata title"),
        ("---\nowner: team\n---\n# Heading fallback", "Heading fallback"),
        ("---\ntitle: ''\n---\n# Empty title fallback", "Empty title fallback"),
        ("```md\n# Not the title\n```\n# Actual title ##", "Actual title"),
        ("~~~\n# Example\n~~~\n# Real heading", "Real heading"),
        ("\ufeff# BOM heading\n", "BOM heading"),
        ("Body only\n## Subheading", ""),
    ],
)
def test_document_title_metadata_and_heading_rules(tmp_path, content, expected):
    path = tmp_path / "title.md"
    path.write_text(content, encoding="utf-8")
    assert search_tools._extract_document_title(path) == expected


def test_discover_title_errors_are_not_reported_as_empty_metadata(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError):
        search_tools._extract_document_title(tmp_path / "missing.md")
    path = tmp_path / "large.md"
    path.write_text("# Long heading", encoding="utf-8")
    monkeypatch.setattr(search_tools, "_MAX_DOCUMENT_BYTES", 4)
    with pytest.raises(ValueError, match="size limit"):
        search_tools._extract_document_title(path)


async def test_read_authority_is_rechecked_and_cannot_escape_source(knowledge, store, tmp_path):
    root, tools = knowledge
    entry = tools["knowledge_read"]
    ctx = context("knowledge_read")
    assert not (await entry.authorize(ctx, {"kb_id": 2})).allowed
    with pytest.raises(ToolFailed, match="authorized"):
        await entry.tool.function(ctx, 2, "secret.md")
    with pytest.raises(ToolFailed, match="Invalid knowledge base path"):
        await entry.tool.function(ctx, 1, "../2/secret.md")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (root / "1" / "escape.md").symlink_to(outside)
    with pytest.raises(ToolFailed, match="escapes"):
        await entry.tool.function(ctx, 1, "escape.md")
    with store.sessions.begin() as db:
        db.delete(db.get(KnowledgeBase, 1))
    with pytest.raises(ToolFailed, match="installed"):
        await entry.tool.function(ctx, 1, "retry.md")
    assert not (
        await entry.authorize(context("knowledge_read", actor="other"), {"kb_id": 2})
    ).allowed


async def test_agent_capability_revocation_is_checked_at_execution(knowledge, store):
    _, tools = knowledge
    with store.sessions.begin() as db:
        agent = Agent(name="reader", prompt="Read", tools=["knowledge_read"], status="active")
        db.add(agent)
        db.flush()
        agent_id = agent.id
    ctx = context("knowledge_read", agent_id=agent_id)
    entry = tools["knowledge_read"]
    assert (await entry.authorize(ctx, {"kb_id": 1})).allowed
    with store.sessions.begin() as db:
        db.get(Agent, agent_id).tools = []
    assert not (await entry.authorize(ctx, {"kb_id": 1})).allowed
    with pytest.raises(ToolFailed, match="authorized"):
        await entry.tool.function(ctx, 1, "retry.md")


async def test_search_timeout_is_a_failure_not_an_empty_result(knowledge, monkeypatch):
    _, tools = knowledge

    def timeout(*args, **kwargs):
        raise RuntimeError("Knowledge operation timed out; no complete result is available")

    monkeypatch.setattr(search_tools, "_run_command", timeout)
    with pytest.raises(ToolFailed, match="timed out"):
        await tools["knowledge_search"].tool.function(context("knowledge_search"), 1, "retry")


def test_real_search_process_deadline_and_output_limit_are_enforced():
    with pytest.raises(RuntimeError, match="timed out"):
        search_tools._run_command(
            [sys.executable, "-c", "import time; time.sleep(20)"], timeout=0.05
        )


def test_search_context_keeps_file_and_line_provenance_separate(knowledge):
    root, _ = knowledge
    (root / "1" / "first.md").write_text("First source\nTOKEN\nFirst suffix\n")
    (root / "1" / "second.md").write_text("Second source\nTOKEN\nSecond suffix\n")
    found = search_tools.search(1, "TOKEN", context_lines=1)
    by_file = {item["file"]: item for item in found}
    assert by_file["first.md"]["context"] == "1: First source\n2: TOKEN\n3: First suffix"
    assert by_file["second.md"]["context"] == "1: Second source\n2: TOKEN\n3: Second suffix"
    with pytest.raises(RuntimeError, match="output limit"):
        search_tools._run_command([sys.executable, "-c", "print('x' * 1000000)"], max_bytes=1000)


async def test_empty_scope_does_not_fall_back_to_all_knowledge(knowledge):
    _, tools = knowledge
    listing = await tools["knowledge_list"].tool.function(context("knowledge_list", ids=()))
    assert listing == {"knowledge_bases": [], "total": 0, "next_offset": None}
    assert not (
        await tools["knowledge_read"].authorize(context("knowledge_read", ids=()), {"kb_id": 1})
    ).allowed


def test_public_capability_catalogue_is_the_native_tool_catalogue(knowledge, store):
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    app = FastAPI()
    app.state.agent_runtime = runtime
    app.include_router(capabilities_router, prefix="/api/v1")
    with TestClient(app) as client:
        response = client.get("/api/v1/capabilities")
    assert response.status_code == 200
    tools = response.json()["tools"]
    assert {item["name"] for item in tools} == set(runtime.tools)
    assert KNOWLEDGE_TOOLS <= {item["name"] for item in tools}
    assert all("_runtime" not in item["parameters"].get("properties", {}) for item in tools)
    assert (
        "query"
        not in next(item for item in tools if item["name"] == "knowledge_search")["parameters"][
            "properties"
        ]
    )


async def test_invalid_pattern_and_excessive_output_fail_honestly(knowledge, monkeypatch):
    root, tools = knowledge
    search = tools["knowledge_search"].tool.function
    with pytest.raises(ToolFailed, match="regex"):
        await search(context("knowledge_search"), 1, "(")
    monkeypatch.setattr(search_tools, "_MAX_OUTPUT_BYTES", 100)
    with pytest.raises(ToolFailed, match="output limit"):
        await search(context("knowledge_search"), 1, "Budget")
    (root / "1" / "large.md").write_text("x" * 2_000_001)
    with pytest.raises(ToolFailed, match="size limit"):
        await tools["knowledge_read"].tool.function(context("knowledge_read"), 1, "large.md")


async def test_git_version_and_commit_mismatch_never_silently_reads_current(knowledge, monkeypatch):
    root, tools = knowledge

    async def resolved(**kwargs):
        assert kwargs == {"kb_ids": [1], "db_type": None, "version": "8.0"}
        return [
            search_tools.SearchTarget(
                kb_id=1,
                source_type="git",
                root=root / "1",
                resolved_version="8.0",
                commit_sha="a" * 40,
            )
        ]

    monkeypatch.setattr(search_tools, "resolve_search_targets", resolved)

    def read(*args, **kwargs):
        pytest.fail("Changed commit must be rejected before reading")

    monkeypatch.setattr(search_tools, "read", read)
    with pytest.raises(ToolFailed, match="version changed"):
        await tools["knowledge_read"].tool.function(
            context("knowledge_read"), 1, "retry.md", version="8.0", expected_commit="b" * 40
        )


async def test_main_run_receives_knowledge_failure_and_read_evidence_without_nested_model(
    knowledge, store
):
    runtime = RuntimeApplication(sessions=store.sessions, models=ModelFactory(lambda: config()))
    runtime.store.create_conversation(
        "knowledge-run", "local", scene={"knowledge_base_ids": [1], "datasource_ids": []}
    )
    script = Script(
        [call("knowledge_read", json.dumps({"kb_id": 2, "path": "secret.md"}), "denied")],
        [call("knowledge_search", json.dumps({"kb_id": 1, "pattern": "Budget"}), "search")],
        [call("knowledge_read", json.dumps({"kb_id": 1, "path": "retry.md"}), "read")],
        ["文档说明最多尝试 3 次、间隔 7 秒。"],
    )
    runtime.service.model_factory = script.factory
    await runtime.start()
    try:
        definition = runtime.resolve("knowledge-run", "local", {})
        with pytest.raises(ValueError, match="unavailable knowledge base"):
            runtime.resolve("knowledge-run", "local", {"knowledge_base_ids": [99]})
        run = runtime.service.submit("knowledge-run", "local", "read", "说明重试策略", definition)
        async with asyncio.timeout(10):
            while runtime.store.get(run["id"], "local")["status"] in {"queued", "running"}:
                await asyncio.sleep(0.01)
        final = runtime.store.get(run["id"], "local")
        assert final["status"] == "finished", final
        calls = {item["call_id"]: item for item in final["tool_calls"]}
        assert calls["denied"]["status"] == "failed"
        assert calls["read"]["result"]["content"]["content"].startswith("1: # Retry policy")
        assert len(script.requests) == 4
        evidence = returns(script.requests[-1][0])
        assert any(
            part.tool_call_id == "read" and "content_hash" in part.content for part in evidence
        )
        assert final["approvals"] == []
    finally:
        await runtime.close()
