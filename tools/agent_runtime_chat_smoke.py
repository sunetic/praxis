"""Real HTTP/SSE smoke against a running product service; no fake model or tool.

Run with: uv run python tools/agent_runtime_chat_smoke.py --url http://127.0.0.1:8011
The service must already have a model and an explicitly authorized test datasource.
This is an initial integration smoke, not the plan's full acceptance suite.
"""

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx

PROMPTS = [
    "请用中文列出数据库事务 ACID 的四个性质，每项一句话。不要查询数据库。",
    "只展开你刚才的第二项，用一个转账的例子，控制在两句话内。",
    "查看当前已授权的数据源，然后在其中一个数据库执行只读查询 SELECT 7 AS runtime_probe，告诉我实际返回值。不要修改任何数据。",
]


async def run(url: str, output: Path):
    report = {"kind": "product-http-smoke", "url": url, "turns": [], "acceptance_complete": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(base_url=url, timeout=180, trust_env=False) as client:
        created = await client.post("/api/v1/conversations", json={"title": "Native runtime smoke"})
        created.raise_for_status()
        conversation = report["conversation_id"] = created.json()["id"]
        for prompt in PROMPTS:
            started = time.monotonic()
            response = await client.post(
                f"/api/v1/conversations/{conversation}/runs",
                json={
                    "client_request_id": uuid.uuid4().hex,
                    "prompt": prompt,
                },
            )
            response.raise_for_status()
            run_id = response.json()["id"]
            turn = {
                "prompt": prompt,
                "run_id": run_id,
                "events": [],
                "first_text_seconds": None,
                "first_tool_seconds": None,
                "visible_text": "",
            }
            report["turns"].append(turn)
            try:
                async with client.stream("GET", f"/api/v1/runs/{run_id}/events") as stream:
                    stream.raise_for_status()
                    async for line in stream.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        event = json.loads(line[6:])
                        event["received_seconds"] = time.monotonic() - started
                        turn["events"].append(event)
                        if event["type"] == "assistant_delta":
                            turn["visible_text"] += event["text"]
                            if turn["first_text_seconds"] is None:
                                turn["first_text_seconds"] = event["received_seconds"]
                        if event["type"] == "tool_start" and turn["first_tool_seconds"] is None:
                            turn["first_tool_seconds"] = event["received_seconds"]
                        if event["type"] == "run_paused":
                            raise AssertionError("Read-only smoke unexpectedly requested approval")
                state = await client.get(f"/api/v1/runs/{run_id}")
                state.raise_for_status()
                turn["state"] = state.json()
                turn["total_seconds"] = time.monotonic() - started
                print(
                    json.dumps(
                        {
                            "turn": len(report["turns"]),
                            "status": turn["state"]["status"],
                            "first_text_seconds": turn["first_text_seconds"],
                            "text": turn["visible_text"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                assert turn["state"]["status"] == "finished", turn["state"]["error_code"]
                assert [e["seq"] for e in turn["events"]] == list(
                    range(1, turn["state"]["event_seq"] + 1)
                )
                if len(report["turns"]) <= 2:
                    assert not turn["state"]["tool_calls"], (
                        "A direct explanation made an unnecessary tool call"
                    )
                else:
                    assert any(
                        call["name"] == "query_database"
                        and call["status"] == "succeeded"
                        and call["result"]["content"]["rows"] == [{"runtime_probe": 7}]
                        for call in turn["state"]["tool_calls"]
                    ), "No successful real database probe"
            finally:
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        history = await client.get(f"/api/v1/conversations/{conversation}/runs")
        history.raise_for_status()
        assert len(history.json()) == len(PROMPTS)
    print(f"HTTP smoke passed; review the complete conversation in {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8011")
    parser.add_argument("--output", type=Path, default=Path("tmp/agent-runtime-product-smoke.json"))
    args = parser.parse_args()
    asyncio.run(run(args.url, args.output))
