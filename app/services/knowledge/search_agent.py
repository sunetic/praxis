from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.logging import fmt_kv, get_logger
from app.services.agent.reasoning_engine import EngineConfig, ReasoningEngine, SimpleToolExecutor
from app.services.knowledge.query_expansion import QueryPlan, build_query_plan
from app.services.knowledge.search_tools import (
    TOOL_SCHEMAS,
    KnowledgeToolExecutor,
    SearchTarget,
    resolve_search_targets,
    target_document_count,
)
from app.services.llm import LLMClient, get_llm_client
from app.services.platform.prompt_loader import PromptLoader

logger = get_logger("knowledge.search_agent")


class KnowledgeSearchAgent:
    def __init__(
        self,
        *,
        llm_client: LLMClient | None = None,
        max_iterations: int = 10,
        max_reflections: int = 3,
    ) -> None:
        self._llm = llm_client or get_llm_client()
        self._max_iterations = max_iterations
        self._max_reflections = max_reflections

    async def run(
        self,
        *,
        query: str,
        kb_ids: list[int] | None = None,
        db_type: str | None = None,
        version: str | None = None,
        knowledge_bases: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        targets = await resolve_search_targets(
            kb_ids=kb_ids,
            db_type=db_type,
            version=version,
        )
        query_plan = build_query_plan(query)
        if not targets:
            return self._build_result(
                "No installed knowledge bases are available.",
                [],
                targets=[],
                query_plan=query_plan,
                coverage={
                    "searched_term_groups": {},
                    "uncovered_groups": {},
                    "coverage_complete": False,
                    "searched_patterns": [],
                    "target_coverage": {},
                },
            )

        target_items = await self._target_items(targets, knowledge_bases)
        system_prompt = PromptLoader.render(
            "knowledge/prompts/knowledge_search.tpl",
            knowledge_bases=target_items,
            query_plan_json=json.dumps(query_plan.to_prompt_dict(), ensure_ascii=False),
        )

        knowledge_executor = KnowledgeToolExecutor(targets, query_plan)
        engine = ReasoningEngine(
            config=EngineConfig(
                max_iterations=self._max_iterations,
                max_reflections=self._max_reflections,
            ),
            llm=self._llm,
            tool_executor=SimpleToolExecutor(knowledge_executor.execute),
        )

        final_text = ""
        tool_results: list[dict[str, Any]] = []
        async for event in engine.run(
            messages=[{"role": "user", "content": query}],
            tools=TOOL_SCHEMAS,
            system_prompt=system_prompt,
        ):
            event_type = event.get("type", "")
            if event_type == "assistant":
                final_text += event.get("data", {}).get("text", "")
            elif event_type == "tool_result":
                data = event.get("data", {})
                result = data.get("result", {})
                if isinstance(result, dict) and result.get("success"):
                    tool_results.append(
                        {
                            "tool": data.get("name", ""),
                            "data": result.get("data"),
                        }
                    )

        coverage = knowledge_executor.coverage_report()
        logger.info(
            "knowledge_search_complete %s",
            fmt_kv(
                query=query[:80],
                tool_calls=len(tool_results),
                response_len=len(final_text),
                target_count=len(targets),
                versions=[target.resolved_version for target in targets],
                commits=[target.commit_sha for target in targets],
                coverage_complete=coverage["coverage_complete"],
            ),
        )
        return self._build_result(
            final_text,
            tool_results,
            targets=targets,
            query_plan=query_plan,
            coverage=coverage,
        )

    async def _target_items(
        self,
        targets: list[SearchTarget],
        supplied: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        supplied_by_id = {
            int(item["id"]): item
            for item in supplied or []
            if isinstance(item, dict) and item.get("id") is not None
        }
        counts = await asyncio.gather(
            *(asyncio.to_thread(target_document_count, target) for target in targets)
        )
        results: list[dict[str, Any]] = []
        for target, count in zip(targets, counts, strict=True):
            provided = supplied_by_id.get(target.kb_id, {})
            item = {
                "id": target.kb_id,
                "name": provided.get("name") or target.pack_id or str(target.kb_id),
                "doc_count": count,
                "db_type": target.db_type,
                "version": target.resolved_version,
                "commit_sha": target.commit_sha,
                "source_type": target.source_type,
            }
            results.append(item)
        return results

    def _build_result(
        self,
        final_text: str,
        tool_results: list[dict[str, Any]],
        *,
        targets: list[SearchTarget],
        query_plan: QueryPlan,
        coverage: dict[str, Any],
    ) -> dict[str, Any]:
        snippets: list[dict[str, Any]] = []
        for tool_result in tool_results:
            data = tool_result.get("data")
            if tool_result["tool"] == "kb_search" and isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict) or "file" not in item:
                        continue
                    snippets.append(
                        {
                            "kb_id": item.get("kb_id"),
                            "file": item["file"],
                            "line": item.get("line", 0),
                            "content": item.get("context") or item.get("match", ""),
                            "version": item.get("version"),
                            "commit_sha": item.get("commit_sha"),
                        }
                    )
            elif tool_result["tool"] == "kb_read" and isinstance(data, dict):
                content = data.get("content", "")
                if content:
                    snippets.append(
                        {
                            "kb_id": data.get("kb_id"),
                            "file": data.get("file") or "(read)",
                            "line": data.get("start_line", 0),
                            "content": content[:2000],
                            "version": data.get("version"),
                            "commit_sha": data.get("commit_sha"),
                        }
                    )

        found = "none"
        if snippets:
            found = (
                "complete"
                if coverage.get("coverage_complete") and final_text.strip()
                else "partial"
            )

        suggestion = None
        if found != "complete":
            suggestion = (
                "Some keyword groups were not searched; refine the query or inspect additional "
                "documents."
                if coverage.get("uncovered_groups")
                else "Try refining the query or adding more documents to the knowledge base."
            )

        sources = [target.provenance() for target in targets]
        return {
            "found": found,
            "snippets": snippets[:20],
            "summary": final_text[:3000] if final_text else "",
            "suggestion": suggestion,
            "query_plan": query_plan.to_prompt_dict(),
            **coverage,
            "sources": sources,
            "requested_version": sources[0].get("requested_version") if len(sources) == 1 else None,
            "resolved_version": sources[0].get("resolved_version") if len(sources) == 1 else None,
            "commit_sha": sources[0].get("commit_sha") if len(sources) == 1 else None,
        }
