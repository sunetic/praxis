"""Real HTTP/model Skill authoring; use only an isolated configured service.

No scripted model or response processing. Saves drafts and explicitly installs
one generated Skill only after checking it is not always-active. Nothing executes
the generated instructions. Raw outputs/events remain available for human review.
"""

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx


async def run(url, output):
    report = {"kind": "native-skill-http", "turns": [], "acceptance_complete": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(base_url=url, timeout=180, trust_env=False) as client:
            before = await client.get("/api/v1/skills")
            before.raise_for_status()
            before_names = {item["name"] for item in before.json()}
            response = await client.post("/api/v1/skill-drafts")
            response.raise_for_status()
            draft_id = report["draft_id"] = response.json()["id"]
            response = await client.post(
                "/api/v1/conversations",
                json={
                    "title": "Skill 原生构建自测",
                    "scene": {
                        "skill_draft_ids": [draft_id],
                        "datasource_ids": [],
                        "knowledge_base_ids": [],
                        "service_ids": [],
                    },
                },
            )
            response.raise_for_status()
            conversation_id = report["conversation_id"] = response.json()["id"]
            name = report["skill_name"] = "evidence-review-" + uuid.uuid4().hex[:8]
            prompts = [
                f"请创建并保存一个 Skill 草稿，名称为 {name}，版本1.0.0，适用 PostgreSQL，不要设为 always_apply，也不要安装。用途：阅读用户提供的查询日志，区分事实和推断，证据不足时说明缺什么；不得自行运行SQL、命令或修改数据库。写中文说明和正文，内容简洁可复用，不编造具体日志。",
                "只把适用数据库改成 MySQL，其他内容保持不变，保存草稿。回答一句话。",
                "解释当前这份 Skill 何时适用，不要修改草稿，也不要安装。两句话即可。",
            ]
            for prompt in prompts:
                started = time.monotonic()
                response = await client.post(
                    f"/api/v1/conversations/{conversation_id}/runs",
                    json={
                        "client_request_id": uuid.uuid4().hex,
                        "prompt": prompt,
                    },
                )
                response.raise_for_status()
                run_id = response.json()["id"]
                turn = {"prompt": prompt, "run_id": run_id, "events": [], "visible_text": ""}
                report["turns"].append(turn)
                async with client.stream("GET", f"/api/v1/runs/{run_id}/events") as stream:
                    stream.raise_for_status()
                    async for line in stream.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        event = json.loads(line[5:])
                        event["received_seconds"] = time.monotonic() - started
                        turn["events"].append(event)
                        if event["type"] == "assistant_delta":
                            turn["visible_text"] += event["text"]
                        if event["type"] == "run_paused":
                            await client.post(f"/api/v1/runs/{run_id}/cancel")
                            raise AssertionError(
                                "This draft-only task must not dispatch an approval action"
                            )
                response = await client.get(f"/api/v1/runs/{run_id}")
                response.raise_for_status()
                turn["state"] = response.json()
                response = await client.get(f"/api/v1/skill-drafts/{draft_id}")
                response.raise_for_status()
                turn["draft"] = response.json()
                turn["seconds"] = time.monotonic() - started
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
                print(
                    json.dumps(
                        {
                            "turn": len(report["turns"]),
                            "text": turn["visible_text"],
                            "seconds": turn["seconds"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                assert turn["state"]["status"] == "finished", turn["state"]["error_code"]
            first, second, third = [turn["draft"] for turn in report["turns"]]
            assert first["content"]["name"] == name
            assert first["content"]["database"] == "postgresql"
            assert first["content"]["always_apply"] is False
            assert first["content"]["prompt"].strip()
            assert second["content"] == {**first["content"], "database": "mysql"}
            assert third == second, "Explanation unexpectedly changed the draft"
            response = await client.get("/api/v1/skills")
            response.raise_for_status()
            after_names = {item["name"] for item in response.json()}
            report["skills_before_install"] = sorted(after_names)
            assert before_names <= after_names
            assert name not in after_names, (
                "The target Skill was installed before explicit user action"
            )
            installed = await client.post("/api/v1/skills", json=third["content"])
            report["install_status"] = installed.status_code
            installed.raise_for_status()
            report["installed"] = installed.json()
            # The public SkillCreate contract trims surrounding whitespace.
            # Preserve both raw values in the report; do not rewrite model text.
            assert report["installed"]["prompt"] == third["content"]["prompt"].strip()
            assert report["installed"]["always_apply"] is False
            report["integration_passed"] = True
    finally:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8011")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.output))
