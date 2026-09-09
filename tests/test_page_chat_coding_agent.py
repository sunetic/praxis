from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.chat import ChatService
from app.services.page.chat_agent import PageChatAgent
from app.services.page.coding_tools import PageCodingTools


def _tool_response(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


def _text_response(content: str) -> dict[str, Any]:
    return {"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]}


class _SequenceLLM:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages, tools=None, stream=False, **kwargs):
        self.calls.append({"messages": messages, "tools": tools, "stream": stream})
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        yield self.responses.pop(0)


@pytest.mark.asyncio
async def test_page_chat_agent_builds_and_verifies_both_sources(tmp_path: Path) -> None:
    (tmp_path / "main.tsx").write_text(
        "export default function Page(){return <main>old</main>}\n", encoding="utf-8"
    )
    (tmp_path / "preview.html").write_text(
        "<!doctype html><html><body><main>old</main></body></html>\n",
        encoding="utf-8",
    )
    source = "export default function Page(){return <main>capacity</main>}\n"
    preview = "<!doctype html><html><body><main>capacity</main></body></html>\n"
    llm = _SequenceLLM(
        [
            _tool_response("read-source", "page_source_read", {"path": "main.tsx"}),
            _tool_response("read-preview", "page_source_read", {"path": "preview.html"}),
            _tool_response(
                "write-source",
                "page_source_replace",
                {"path": "main.tsx", "content": source},
            ),
            _tool_response(
                "write-preview",
                "page_source_replace",
                {"path": "preview.html", "content": preview},
            ),
            _tool_response("check", "page_workspace_check", {}),
            _tool_response(
                "finish",
                "page_build_finish",
                {
                    "outcome": "completed",
                    "assistant_message": "Capacity Page is ready.",
                    "diff_summary": "Updated Page source and preview",
                },
            ),
            _text_response("Capacity Page is ready."),
        ]
    )
    events: list[dict[str, Any]] = []
    agent = PageChatAgent(chat_service=ChatService(llm=llm))

    result = await agent.run_coding_task(
        goal="Build a capacity Page",
        workspace_dir=tmp_path,
        max_iterations=10,
        event_callback=events.append,
    )

    assert result.result_status == "completed"
    assert result.changed_files == ["main.tsx", "preview.html"]
    assert (tmp_path / "main.tsx").read_text(encoding="utf-8") == source
    assert (tmp_path / "preview.html").read_text(encoding="utf-8") == preview
    assert all(call["stream"] is True for call in llm.calls)
    assert any(
        event.get("payload", {}).get("agent_event_type") == "tool_result" for event in events
    )


@pytest.mark.asyncio
async def test_page_workspace_check_requires_synchronized_preview(tmp_path: Path) -> None:
    (tmp_path / "main.tsx").write_text(
        "export default function Page(){return <main>old</main>}\n", encoding="utf-8"
    )
    (tmp_path / "preview.html").write_text(
        "<!doctype html><html><body><main>old</main></body></html>\n",
        encoding="utf-8",
    )
    tools = PageCodingTools(workspace_dir=tmp_path)
    await tools.execute(
        "page_source_replace",
        {
            "path": "main.tsx",
            "content": "export default function Page(){return <main>new</main>}\n",
        },
    )

    check = await tools.execute("page_workspace_check", {})
    finish = await tools.execute(
        "page_build_finish",
        {"outcome": "completed", "assistant_message": "Done"},
    )

    assert check["success"] is False
    assert "preview.html must be updated" in check["error"]["message"]
    assert finish["success"] is False
    assert finish["error"]["code"] == "verification_required"
