"""Real HTTP authoring conversation; no test-time answer or source injection.

This checks integration and version facts, not complete Function acceptance.
The isolated service must have a real model and automatic schedules disabled.
"""

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx


async def run(url: str, output: Path):
    report = {"kind": "native-function-chat", "acceptance_complete": False, "turns": []}
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(base_url=url, timeout=180, trust_env=False) as client:
            health = await client.get("/api/v1/schedules/worker-health")
            health.raise_for_status()
            assert health.json()["autostart"] is False
            function = await client.post("/api/v1/functions", json={"name": "订单金额汇总自测"})
            function.raise_for_status()
            report["function"] = function.json()
            function_id = function.json()["id"]
            conversation = await client.post(
                "/api/v1/conversations",
                json={
                    "title": "Function 原生构建自测",
                    "scene": {"function_ids": [function_id], "datasource_ids": []},
                },
            )
            conversation.raise_for_status()
            report["conversation_id"] = conversation.json()["id"]
            prompts = [
                "请为当前 Function 编写订单金额汇总：payload 的 orders 是对象数组，每项 amount 是数字，返回 count 和 total；缺少 orders 或 amount 不是数字时抛出 ValueError。只保存草稿并执行可用的检查，不要发布，不查询数据库。用中文简短说明结果和未验证项。",
                "改一下：空 orders 数组应当返回 count=0、total=0；如果有负数金额，抛出 ValueError。保留其他要求，更新草稿并检查，仍然不要发布。",
                "现在只解释哪些检查真的执行了，哪些没有；两句话内，不要再改代码或调用工具。",
            ]
            for prompt in prompts:
                started = time.monotonic()
                response = await client.post(
                    f"/api/v1/conversations/{report['conversation_id']}/runs",
                    json={"client_request_id": uuid.uuid4().hex, "prompt": prompt},
                )
                response.raise_for_status()
                run_id = response.json()["id"]
                turn = {"prompt": prompt, "run_id": run_id, "events": [], "visible_text": ""}
                report["turns"].append(turn)
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
                        if event["type"] == "run_paused":
                            raise AssertionError("Draft-only request unexpectedly asked to publish")
                result = await client.get(f"/api/v1/runs/{run_id}")
                result.raise_for_status()
                turn["run"] = result.json()
                draft = await client.get(f"/api/v1/functions/{function_id}/draft")
                draft.raise_for_status()
                turn["draft"] = draft.json()
                turn["seconds"] = time.monotonic() - started
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
                print(
                    json.dumps(
                        {
                            "turn": len(report["turns"]),
                            "status": turn["run"]["status"],
                            "text": turn["visible_text"],
                            "revision": turn["draft"]["revision_id"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                assert turn["run"]["status"] == "finished"
                assert turn["draft"]["current_release_id"] is None
                if len(report["turns"]) < 3:
                    assert turn["draft"]["revision_id"]
                    assert turn["draft"]["validation"], "No actual validation record"
                    assert any(
                        call["name"] == "function_validate" and call["status"] == "succeeded"
                        for call in turn["run"]["tool_calls"]
                    )
                else:
                    assert not turn["run"]["tool_calls"]
                    assert (
                        turn["draft"]["revision_id"] == report["turns"][1]["draft"]["revision_id"]
                    )
            assert (
                report["turns"][0]["draft"]["revision_id"]
                != report["turns"][1]["draft"]["revision_id"]
            )
            report["integration_checks_passed"] = True
    finally:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(run(arguments.url, arguments.output))
