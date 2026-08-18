"""Run live-LLM retention evaluations for persistent conversation compaction."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.chat_history import format_messages_for_llm
from app.db.database import Base
from app.models import models
from app.services.agent.context_budget import estimate_messages_tokens
from app.services.chat.context_manager import ConversationContextManager
from app.services.llm import get_llm_client

SCENARIOS_PATH = Path(__file__).with_name("scenarios.json")
PADDING = " 诊断证据保持可追溯。" * 22


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict[str, Any]]:
    """Load and minimally validate the versioned scenario catalog."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios")
    if payload.get("version") != 1 or not isinstance(scenarios, list) or not scenarios:
        raise ValueError("context compaction scenarios must use version=1 and be non-empty")
    for scenario in scenarios:
        required = {
            "id",
            "title",
            "turns",
            "required_memory_terms",
            "forbidden_memory_terms",
            "questions",
        }
        missing = sorted(required - set(scenario))
        if missing:
            raise ValueError(f"scenario {scenario.get('id', '<unknown>')} missing: {missing}")
    return scenarios


async def _ask(llm: Any, context: list[dict[str, Any]], prompt: str) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "Answer the question only from the supplied conversation context. "
                "Be concise, preserve exact identifiers, and say unknown when evidence is absent."
            ),
        },
        *context,
        {"role": "user", "content": prompt},
    ]
    response: dict[str, Any] | None = None
    async for chunk in llm.chat(
        messages=messages,
        tools=None,
        stream=False,
        temperature=0,
    ):
        response = chunk
        break
    return str(
        (((response or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    ).strip()


def score_answer(answer: str, question: dict[str, Any]) -> dict[str, Any]:
    """Score exact fact groups and obvious distractor leakage deterministically."""

    normalized = answer.casefold()
    groups = question.get("expected_groups") or []
    group_results = [any(str(term).casefold() in normalized for term in group) for group in groups]
    leaked = [
        term for term in question.get("forbidden") or [] if str(term).casefold() in normalized
    ]
    recall = sum(group_results) / len(group_results) if group_results else 1.0
    return {
        "recall": round(recall, 3),
        "expected_groups_passed": sum(group_results),
        "expected_groups_total": len(group_results),
        "distractor_leaks": leaked,
        "passed": recall == 1.0 and not leaked,
    }


def _seed_scenario(db: Session, scenario: dict[str, Any]) -> tuple[int, list[models.Message]]:
    conversation = models.Conversation(title=f"context-eval:{scenario['id']}")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    for user_text, assistant_text in scenario["turns"]:
        db.add_all(
            [
                models.Message(
                    conversation_id=conversation.id,
                    role="user",
                    content=f"{user_text}{PADDING}",
                ),
                models.Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=f"{assistant_text}{PADDING}",
                ),
            ]
        )
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
    messages = (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation.id)
        .order_by(models.Message.id.asc())
        .all()
    )
    return conversation.id, messages


async def run_scenario(scenario: dict[str, Any], llm: Any) -> dict[str, Any]:
    """Compare question recall before and after one real compaction pass."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    try:
        conversation_id, raw_messages = _seed_scenario(db, scenario)
        raw_context = format_messages_for_llm(raw_messages)
        manager = ConversationContextManager(llm_client=llm)
        preview, compression_required = manager.preview(
            db,
            conversation_id=conversation_id,
            raw_messages=raw_messages,
            system_prompt="You are a database operations assistant.",
            tools=None,
        )
        if not compression_required:
            raise RuntimeError(f"scenario {scenario['id']} did not reach the compaction threshold")
        prepared = await manager.prepare(
            db,
            conversation_id=conversation_id,
            raw_messages=raw_messages,
            system_prompt="You are a database operations assistant.",
            tools=None,
        )
        if prepared.compression is None:
            raise RuntimeError(f"scenario {scenario['id']} failed to produce a valid snapshot")
        memory = next(
            str(message.get("content") or "")
            for message in prepared.messages
            if message.get("role") == "system"
            and "<conversation_memory>" in str(message.get("content") or "")
        )
        required_missing = [
            term
            for term in scenario["required_memory_terms"]
            if term.casefold() not in memory.casefold()
        ]
        forbidden_retained = [
            term
            for term in scenario["forbidden_memory_terms"]
            if term.casefold() in memory.casefold()
        ]
        question_results: list[dict[str, Any]] = []
        for question in scenario["questions"]:
            baseline_answer = await _ask(llm, raw_context, question["prompt"])
            compacted_answer = await _ask(llm, prepared.messages, question["prompt"])
            baseline_score = score_answer(baseline_answer, question)
            compacted_score = score_answer(compacted_answer, question)
            question_results.append(
                {
                    "prompt": question["prompt"],
                    "baseline_answer": baseline_answer,
                    "compacted_answer": compacted_answer,
                    "baseline": baseline_score,
                    "compacted": compacted_score,
                }
            )
        baseline_recall = sum(item["baseline"]["recall"] for item in question_results) / len(
            question_results
        )
        compacted_recall = sum(item["compacted"]["recall"] for item in question_results) / len(
            question_results
        )
        retention = compacted_recall / baseline_recall if baseline_recall else 0.0
        before_tokens = estimate_messages_tokens(raw_context)
        after_tokens = estimate_messages_tokens(prepared.messages)
        passed = (
            not required_missing
            and not forbidden_retained
            and compacted_recall >= 0.9
            and retention >= 0.9
            and all(not item["compacted"]["distractor_leaks"] for item in question_results)
            and after_tokens < before_tokens
        )
        return {
            "id": scenario["id"],
            "title": scenario["title"],
            "passed": passed,
            "preview": preview,
            "compression": prepared.compression,
            "memory_checks": {
                "required_missing": required_missing,
                "forbidden_retained": forbidden_retained,
            },
            "metrics": {
                "baseline_recall": round(baseline_recall, 3),
                "compacted_recall": round(compacted_recall, 3),
                "retention_ratio": round(retention, 3),
                "before_tokens": before_tokens,
                "after_tokens": after_tokens,
                "token_reduction_percent": round((1 - after_tokens / before_tokens) * 100, 1),
            },
            "questions": question_results,
        }
    finally:
        db.close()
        engine.dispose()


async def async_main(args: argparse.Namespace) -> int:
    scenarios = load_scenarios(Path(args.scenarios))
    selected = (
        scenarios
        if args.scenario == "all"
        else [scenario for scenario in scenarios if scenario["id"] == args.scenario]
    )
    if not selected:
        raise ValueError(f"unknown scenario: {args.scenario}")
    llm = get_llm_client()
    results = [await run_scenario(scenario, llm) for scenario in selected]
    report = {
        "suite": "context_compaction",
        "scenario_count": len(results),
        "passed": all(result["passed"] for result in results),
        "results": results,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(f"wrote {output}")
    else:
        print(rendered)
    return 0 if report["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="all", help="scenario id or 'all'")
    parser.add_argument("--scenarios", default=str(SCENARIOS_PATH))
    parser.add_argument("--output", help="optional JSON report path")
    return asyncio.run(async_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
