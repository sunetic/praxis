from __future__ import annotations

import json
from typing import Any

from app.core.logging import fmt_kv, get_logger
from app.services.agent.reasoning_engine import (
    EngineConfig,
    ReasoningEngine,
    SimpleToolExecutor,
)
from app.services.knowledge.search_tools import TOOL_SCHEMAS, execute_tool
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
        knowledge_bases: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not knowledge_bases:
            knowledge_bases = self._default_kb_list(kb_ids)

        system_prompt = PromptLoader.render(
            "knowledge/prompts/knowledge_search.tpl",
            knowledge_bases=knowledge_bases,
        )

        engine = ReasoningEngine(
            config=EngineConfig(
                max_iterations=self._max_iterations,
                max_reflections=self._max_reflections,
            ),
            llm=self._llm,
            tool_executor=SimpleToolExecutor(execute_tool),
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
                    tool_results.append({
                        "tool": data.get("name", ""),
                        "data": result.get("data"),
                    })

        logger.info(
            "knowledge_search_complete %s",
            fmt_kv(
                query=query[:80],
                tool_calls=len(tool_results),
                response_len=len(final_text),
            ),
        )

        return self._build_result(final_text, tool_results)

    def _default_kb_list(self, kb_ids: list[int] | None) -> list[dict[str, Any]]:
        from pathlib import Path
        data_root = Path("data/knowledge")
        if not data_root.is_dir():
            return []
        results = []
        for entry in sorted(data_root.iterdir()):
            if not entry.is_dir():
                continue
            try:
                kid = int(entry.name)
            except ValueError:
                continue
            if kb_ids and kid not in kb_ids:
                continue
            doc_count = sum(1 for _ in entry.rglob("*.md") if _.name.lower() != "readme.md")
            results.append({"id": kid, "name": entry.name, "doc_count": doc_count})
        return results

    def _build_result(
        self,
        final_text: str,
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snippets: list[dict[str, Any]] = []
        for tr in tool_results:
            data = tr.get("data")
            if tr["tool"] == "kb_search" and isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "file" in item:
                        snippets.append({
                            "file": item["file"],
                            "line": item.get("line", 0),
                            "content": item.get("context") or item.get("match", ""),
                        })
            elif tr["tool"] == "kb_read" and isinstance(data, dict):
                content = data.get("content", "")
                if content:
                    snippets.append({
                        "file": "(read)",
                        "line": data.get("start_line", 0),
                        "content": content[:2000],
                    })

        found = "none"
        if snippets:
            found = "complete" if len(final_text) > 50 else "partial"

        return {
            "found": found,
            "snippets": snippets[:20],
            "summary": final_text[:3000] if final_text else "",
            "suggestion": None if found == "complete" else "Try refining the query or adding more documents to the knowledge base.",
        }
