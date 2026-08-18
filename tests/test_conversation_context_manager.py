from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.database import Base
from app.models import models
from app.services.chat.context_manager import (
    ConversationContextManager,
    assess_summary_quality,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _session_factory(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'context-manager.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False), engine


def _valid_memory(prompt: str) -> str:
    source_ids = re.findall(r"message_id=(\d+)", prompt)
    source_id = source_ids[0] if source_ids else "1"
    retained: list[str] = []
    if "CRITICAL_SCHEMA=orders" in prompt:
        retained.append(f"- Schema is `CRITICAL_SCHEMA=orders` [m{source_id}]")
    if "NEW_BOUND=500ms" in prompt:
        retained.append(f"- Latency bound is `NEW_BOUND=500ms` [m{source_id}]")
    if "CRITICAL_SCHEMA=orders" not in prompt and "NEW_BOUND=500ms" not in prompt:
        retained.append(f"- Continue the verified database investigation [m{source_id}]")
    evidence = "\n".join(retained)
    return f"""## Goal
- Diagnose the production query safely [m{source_id}]
## Constraints and User Preferences
- Do not mutate production data [m{source_id}]
## Verified Facts and Evidence
{evidence}
## Decisions
- Continue with read-only evidence [m{source_id}]
## Progress
### Done
- Initial evidence was collected [m{source_id}]
### In Progress
- Query analysis remains active [m{source_id}]
### Blocked or Failed Attempts
- No active blocker is recorded [m{source_id}]
## Referenced Objects and Artifacts
- The orders query is the current object [m{source_id}]
## Open Questions and Next Steps
- Validate the execution plan [m{source_id}]"""


class QualitySummarizer:
    model = "quality-test-model"

    def __init__(self, *, fail_first: bool = False, always_invalid: bool = False) -> None:
        self.fail_first = fail_first
        self.always_invalid = always_invalid
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(self, messages, tools=None, stream=False, temperature=None):
        del tools, stream, temperature
        self.calls.append(messages)
        if self.always_invalid or (self.fail_first and len(self.calls) == 1):
            content = "A loose, repetitive summary.\nA loose, repetitive summary."
        else:
            prompt = "\n".join(str(message.get("content") or "") for message in messages)
            content = _valid_memory(prompt)
        yield {"choices": [{"message": {"content": content}}]}


def _add_turn(
    db: Session,
    conversation_id: int,
    user_content: str,
    assistant_content: str,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
) -> None:
    db.add_all(
        [
            models.Message(
                conversation_id=conversation_id,
                role="user",
                content=user_content,
            ),
            models.Message(
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_content,
                tool_calls=tool_calls,
            ),
        ]
    )


def _seed_long_conversation(db: Session) -> tuple[models.Conversation, list[models.Message]]:
    conversation = models.Conversation(title="long-context")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    _add_turn(
        db,
        conversation.id,
        "Original objective: diagnose safely. " + "初" * 500,
        "I will keep the investigation read-only. " + "始" * 500,
    )
    repeated = "REPEATED_STATUS: still investigating " + "重" * 500
    for index in range(14):
        if index == 0:
            user = "CRITICAL_SCHEMA=orders and no writes allowed. " + "关" * 500
        elif index in {1, 2}:
            user = repeated
        elif index == 3:
            user = "UNRELATED_LUNCH_CHAT: noodles and weekend weather. " + "闲" * 500
        else:
            user = f"Investigation evidence batch {index}. " + "证" * 500
        tool_calls = None
        if index == 12:
            tool_calls = [
                {
                    "id": "call-plan",
                    "name": "execute_sql",
                    "input": {"sql": "EXPLAIN SELECT * FROM orders"},
                    "result": {"rows": [{"type": "range"}]},
                }
            ]
        _add_turn(
            db,
            conversation.id,
            user,
            f"Evidence response {index}. " + "据" * 500,
            tool_calls=tool_calls,
        )
    db.commit()
    messages = (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation.id)
        .order_by(models.Message.id.asc())
        .all()
    )
    return conversation, messages


def _set_small_budget(db: Session) -> None:
    db.add_all(
        [
            models.PlatformSetting(key="context_window_tokens", value=8_192),
            models.PlatformSetting(
                key="context_compression_threshold_percent",
                value=50,
            ),
        ]
    )
    db.commit()


@pytest.mark.anyio
async def test_compaction_removes_irrelevant_and_repeated_content_and_keeps_tail(
    tmp_path: Path,
) -> None:
    factory, engine = _session_factory(tmp_path)
    db = factory()
    try:
        conversation, messages = _seed_long_conversation(db)
        _set_small_budget(db)
        summarizer = QualitySummarizer()
        manager = ConversationContextManager(llm_client=summarizer)

        preview, compression_required = manager.preview(
            db,
            conversation_id=conversation.id,
            raw_messages=messages,
            system_prompt="You are a database assistant.",
            tools=[{"function": {"name": "execute_sql", "description": "Run SQL"}}],
        )
        assert compression_required is True
        assert preview["state"] == "compressing"
        assert preview["compression_progress_percent"] == 100

        prepared = await manager.prepare(
            db,
            conversation_id=conversation.id,
            raw_messages=messages,
            system_prompt="You are a database assistant.",
            tools=[{"function": {"name": "execute_sql", "description": "Run SQL"}}],
        )

        assert prepared.compression is not None
        assert prepared.status["state"] == "ready"
        assert prepared.status["compression_progress_percent"] < 100
        assert prepared.compression["duplicate_messages_omitted"] == 1
        assert prepared.compression["quality"]["passed"] is True
        assert prepared.compression["quality"]["required_section_coverage"] == 1.0
        memory = next(
            message["content"]
            for message in prepared.messages
            if message["role"] == "system" and "<conversation_memory>" in message["content"]
        )
        assert "CRITICAL_SCHEMA=orders" in memory
        assert "UNRELATED_LUNCH_CHAT" not in memory
        assert "REPEATED_STATUS" not in memory
        assert prepared.messages[0]["content"].startswith("Original objective")
        assert prepared.messages[-1]["content"].startswith("Evidence response 13")
        # The newest complete tool-bearing assistant turn remains verbatim.
        tool_messages = [message for message in prepared.messages if message.get("tool_calls")]
        assert tool_messages[0]["tool_calls"][0]["function"]["name"] == "execute_sql"
        snapshot = db.query(models.ConversationContextSnapshot).one()
        assert snapshot.details["quality"]["passed"] is True
        assert snapshot.source_token_count > snapshot.summary_token_count
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_incremental_compaction_merges_previous_memory(tmp_path: Path) -> None:
    factory, engine = _session_factory(tmp_path)
    db = factory()
    try:
        conversation, messages = _seed_long_conversation(db)
        _set_small_budget(db)
        summarizer = QualitySummarizer()
        manager = ConversationContextManager(llm_client=summarizer)
        first = await manager.prepare(
            db,
            conversation_id=conversation.id,
            raw_messages=messages,
            system_prompt="system",
            tools=None,
        )
        assert first.compression and first.compression["revision"] == 1

        for index in range(6):
            marker = "NEW_BOUND=500ms " if index == 0 else f"new batch {index} "
            _add_turn(
                db,
                conversation.id,
                marker + "新" * 500,
                f"new response {index} " + "果" * 500,
            )
        db.commit()
        messages = (
            db.query(models.Message)
            .filter(models.Message.conversation_id == conversation.id)
            .order_by(models.Message.id.asc())
            .all()
        )
        second = await manager.prepare(
            db,
            conversation_id=conversation.id,
            raw_messages=messages,
            system_prompt="system",
            tools=None,
        )

        assert second.compression and second.compression["revision"] == 2
        snapshots = (
            db.query(models.ConversationContextSnapshot)
            .order_by(models.ConversationContextSnapshot.revision.asc())
            .all()
        )
        assert len(snapshots) == 2
        assert "CRITICAL_SCHEMA=orders" in snapshots[1].summary
        assert "NEW_BOUND=500ms" in snapshots[1].summary
        assert "<prior_memory>" in summarizer.calls[-1][1]["content"]
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_quality_failure_retries_then_persists_valid_memory(tmp_path: Path) -> None:
    factory, engine = _session_factory(tmp_path)
    db = factory()
    try:
        conversation, messages = _seed_long_conversation(db)
        _set_small_budget(db)
        summarizer = QualitySummarizer(fail_first=True)
        prepared = await ConversationContextManager(llm_client=summarizer).prepare(
            db,
            conversation_id=conversation.id,
            raw_messages=messages,
            system_prompt="system",
            tools=None,
        )
        assert prepared.compression is not None
        assert prepared.compression["quality"]["attempts"] == 2
        assert len(summarizer.calls) == 2
    finally:
        db.close()
        engine.dispose()


@pytest.mark.anyio
async def test_invalid_summary_keeps_raw_history_and_does_not_persist_snapshot(
    tmp_path: Path,
) -> None:
    factory, engine = _session_factory(tmp_path)
    db = factory()
    try:
        conversation, messages = _seed_long_conversation(db)
        _set_small_budget(db)
        summarizer = QualitySummarizer(always_invalid=True)
        prepared = await ConversationContextManager(llm_client=summarizer).prepare(
            db,
            conversation_id=conversation.id,
            raw_messages=messages,
            system_prompt="system",
            tools=None,
        )
        assert prepared.compression is None
        assert not any(
            "<conversation_memory>" in str(message.get("content") or "")
            for message in prepared.messages
        )
        assert prepared.messages[0]["content"] == messages[0].content
        assert prepared.messages[-1]["content"] == messages[-1].content
        assert db.query(models.ConversationContextSnapshot).count() == 0
        assert len(summarizer.calls) == 2
    finally:
        db.close()
        engine.dispose()


def test_quality_probe_detects_repetition_low_density_and_invalid_evidence() -> None:
    source = [
        models.Message(id=11, conversation_id=1, role="user", content="事实 A " + "证" * 3000)
    ]
    bloated = "\n".join(
        [
            "## Goal",
            "- repeated filler [m999]",
            "- repeated filler [m999]",
        ]
        + ["verbose filler line " + "x" * 300 for _ in range(40)]
    )

    quality = assess_summary_quality(bloated, source)

    assert quality["passed"] is False
    assert "missing_required_sections" in quality["issues"]
    assert "repeated_summary_lines" in quality["issues"]
    assert "invalid_source_references" in quality["issues"]
    assert quality["unique_line_ratio"] < 0.2


def test_quality_probe_accepts_source_references_carried_by_previous_memory() -> None:
    source = [models.Message(id=22, conversation_id=1, role="user", content="New verified fact")]
    previous_memory = _valid_memory("[message_id=11] CRITICAL_SCHEMA=orders")
    updated_memory = previous_memory.replace(
        "## Open Questions and Next Steps",
        "- New verified fact [m22]\n## Open Questions and Next Steps",
    )

    quality = assess_summary_quality(
        updated_memory,
        source,
        previous_summary=previous_memory,
    )

    assert quality["invalid_source_references"] == []
    assert "invalid_source_references" not in quality["issues"]
