"""Real model schedule configuration -> native execution -> Chat follow-up.

Requires an explicitly supplied isolated product server with cron autostart off.
Only test control records are created; the Agent has no tools or data bindings.
"""

import argparse
import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.services.scheduler.triggers import cron_trigger


async def run(url: str, output: Path):
    report = {"kind": "real-schedule-config-and-chat", "acceptance_complete": False, "requests": []}
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(base_url=url, timeout=180, trust_env=False) as client:

            async def post(path, payload):
                started = time.monotonic()
                result = await client.post(path, json=payload)
                data = result.json()
                report["requests"].append(
                    {
                        "path": path,
                        "input": payload,
                        "status": result.status_code,
                        "response": data,
                        "seconds": time.monotonic() - started,
                    }
                )
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
                result.raise_for_status()
                print(json.dumps({"path": path, "response": data}, ensure_ascii=False), flush=True)
                return data

            health = await client.get("/api/v1/schedules/worker-health")
            health.raise_for_status()
            assert health.json()["autostart"] is False, (
                "Use an isolated server with cron autostart disabled"
            )
            agent = await post(
                "/api/v1/agents",
                {
                    "name": "native schedule configuration test",
                    "prompt": "用用户要求的语言和篇幅直接回答。",
                    "tools": [],
                    "skills": [],
                    "datasource_ids": [],
                    "status": "active",
                },
            )
            created = await post(
                "/api/v1/schedules/ai-create",
                {
                    "name": "native configuration integration",
                    "target_type": "agent",
                    "target_id": agent["id"],
                    "prompt": "每个工作日按上海时间上午九点半运行，先保持暂停，不要重试。",
                    "input_prompt": "请用中文两句话解释为什么数据库索引会影响写入性能，不需要调用工具。",
                    "status": "paused",
                    "timezone": "Asia/Shanghai",
                    "max_retries": 0,
                },
            )
            schedule_id = created["schedule"]["id"]
            assert created["schedule"]["status"] == "paused"
            assert created["schedule"]["max_retries"] == 0
            # A paused schedule has no next_run_at. Check its effective calendar
            # too, rather than accidentally accepting a wrong initial time that
            # a later edit happens to overwrite.
            initial = created["schedule"]
            assert initial["schedule_type"] == "cron" and initial["interval_seconds"] is None
            assert cron_trigger(initial["cron_expression"], initial["timezone"]).get_next_fire_time(
                None, datetime(2026, 9, 18, 2, tzinfo=UTC)
            ) == datetime(2026, 9, 21, 1, 30, tzinfo=UTC)
            changed = await post(
                f"/api/v1/schedules/{schedule_id}/build",
                {"prompt": "改为每二十分钟运行一次，继续保持暂停，其余配置不变。"},
            )
            assert changed["schedule"]["interval_seconds"] == 1200
            assert changed["schedule"]["cron_expression"] is None
            assert changed["schedule"]["target_id"] == agent["id"]
            assert changed["schedule"]["status"] == "paused"
            active = await post(
                f"/api/v1/schedules/{schedule_id}/build",
                {
                    "prompt": "改回上海时间每个工作日上午九点半，现在启用。不要改变执行内容或重试次数。"
                },
            )
            next_utc = datetime.fromisoformat(active["schedule"]["next_run_at"]).replace(tzinfo=UTC)
            next_local = next_utc.astimezone(ZoneInfo("Asia/Shanghai"))
            assert next_local.weekday() < 5 and (next_local.hour, next_local.minute) == (9, 30)
            await post(f"/api/v1/schedules/{schedule_id}/pause", {})
            submitted = await post(f"/api/v1/schedules/{schedule_id}/run-now", {})
            occurrence = submitted["run_id"]
            async with asyncio.timeout(180):
                while True:
                    response = await client.get(f"/api/v1/schedules/{schedule_id}/runs")
                    response.raise_for_status()
                    row = next(item for item in response.json() if item["run_id"] == occurrence)
                    if row["status"] not in {"queued", "running"}:
                        break
                    await asyncio.sleep(0.2)
            report["scheduled_result"] = row
            assert row["status"] == "finished", row
            conversation = row["conversation_id"]
            followup = await post(
                f"/api/v1/conversations/{conversation}/runs",
                {
                    "client_request_id": uuid.uuid4().hex,
                    "prompt": "只展开你刚才提到的额外维护成本，用一句中文解释。",
                },
            )
            events = report["chat_events"] = []
            async with client.stream("GET", f"/api/v1/runs/{followup['id']}/events") as stream:
                stream.raise_for_status()
                async for line in stream.aiter_lines():
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        events.append(event)
                        assert event["type"] != "run_paused"
            response = await client.get(f"/api/v1/runs/{followup['id']}")
            response.raise_for_status()
            state = report["chat_result"] = response.json()
            assert state["status"] == "finished" and not state["tool_calls"]
            report["integration_checks_passed"] = True
            print(
                json.dumps(
                    {"scheduled_text": row["output_summary"], "chat_text": state["output"]},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("tmp/agent-runtime-schedule-config-smoke.json")
    )
    args = parser.parse_args()
    asyncio.run(run(args.url, args.output))
