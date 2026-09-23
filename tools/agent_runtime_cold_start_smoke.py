"""Run first after service startup: real concurrent Chat/SSE, health and cancel.

No mock model, response rewriting, or business writes. These observations are
not a replacement for the plan's frozen latency and conversation acceptance set.
"""

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx


async def run(url: str, output: Path, skills: list[str] | None = None):
    report = {
        "kind": "cold-start-http",
        "turns": [],
        "health": [],
        "skills": skills or [],
        "acceptance_complete": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(base_url=url, timeout=180, trust_env=False) as client:
        stop = asyncio.Event()

        async def pulse():
            while not stop.is_set():
                started = time.monotonic()
                response = await client.get("/health")
                report["health"].append(
                    {"seconds": time.monotonic() - started, "status": response.status_code}
                )
                response.raise_for_status()
                try:
                    await asyncio.wait_for(stop.wait(), 0.05)
                except TimeoutError:
                    pass

        async def chat(prompt, *, cancel=False, conversation=None):
            if conversation is None:
                response = await client.post(
                    "/api/v1/conversations",
                    json={
                        "title": "Cold startup probe",
                        "scene": {
                            "datasource_ids": [],
                            "knowledge_base_ids": [],
                            "skills": skills or [],
                        },
                    },
                )
                response.raise_for_status()
                conversation = response.json()["id"]
            turn = {
                "prompt": prompt,
                "conversation_id": conversation,
                "cancel_requested": cancel,
                "events": [],
                "visible_text": "",
            }
            report["turns"].append(turn)
            started = time.monotonic()
            response = await client.post(
                f"/api/v1/conversations/{conversation}/runs",
                json={
                    "prompt": prompt,
                    "client_request_id": uuid.uuid4().hex,
                },
            )
            response.raise_for_status()
            run_id = turn["run_id"] = response.json()["id"]
            turn["accepted_seconds"] = time.monotonic() - started
            async with client.stream("GET", f"/api/v1/runs/{run_id}/events") as stream:
                stream.raise_for_status()
                async for line in stream.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    event["received_seconds"] = time.monotonic() - started
                    turn["events"].append(event)
                    if event["type"] == "assistant_delta":
                        turn.setdefault("first_text_seconds", event["received_seconds"])
                        turn["visible_text"] += event["text"]
                    if cancel and event["type"] == "request_started":
                        response = await client.post(f"/api/v1/runs/{run_id}/cancel")
                        response.raise_for_status()
                        turn["cancel_accepted_seconds"] = time.monotonic() - started
                        cancel = False
            response = await client.get(f"/api/v1/runs/{run_id}")
            response.raise_for_status()
            turn["state"] = response.json()
            turn["total_seconds"] = time.monotonic() - started
            assert turn["state"]["status"] == (
                "cancelled" if turn["cancel_requested"] else "finished"
            )
            assert not turn["state"]["tool_calls"]
            assert [e["seq"] for e in turn["events"]] == list(
                range(1, turn["state"]["event_seq"] + 1)
            )
            print(
                json.dumps(
                    {
                        key: turn[key]
                        for key in ("run_id", "total_seconds", "visible_text", "cancel_requested")
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return conversation

        heartbeat = asyncio.create_task(pulse())
        try:
            conversations = await asyncio.gather(
                *[
                    chat(prompt)
                    for prompt in [
                        "数据库事务的原子性是什么意思？用一个转账例子，两句话说清楚，不查询资料或数据库。",
                        "数据库索引为什么会让写入变慢？用中文两句话解释，不调用工具。",
                        "乐观锁和悲观锁有什么区别？用生活中的例子，中文两句话，不调用工具。",
                    ]
                ]
            )
            await chat(
                "刚才的例子如果中途失败，会怎样？只回答一句话，不调用工具。",
                conversation=conversations[0],
            )
            await chat("请详细解释数据库事务隔离级别及各自的例子，不调用工具。", cancel=True)
            # Observe durable cancellation, not just the initial HTTP response.
            await asyncio.sleep(1)
            response = await client.get(f"/api/v1/runs/{report['turns'][-1]['run_id']}")
            response.raise_for_status()
            assert response.json()["status"] == "cancelled"
            assert response.json()["event_seq"] == report["turns"][-1]["state"]["event_seq"]
            report["integration_passed"] = True
        finally:
            stop.set()
            try:
                await heartbeat
            finally:
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8011")
    parser.add_argument(
        "--output", type=Path, default=Path("tmp/agent-runtime-cold-start-http-01.json")
    )
    parser.add_argument("--skill", action="append", default=[])
    args = parser.parse_args()
    asyncio.run(run(args.url, args.output, args.skill))
