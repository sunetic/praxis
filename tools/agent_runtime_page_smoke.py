"""Real Page Chat API probe, retaining failures and every visible model delta.

This supplies user requirements, not generated source or expected tool sequences.
Compilation, dependency metadata, browser rendering and live binding execution
remain separate facts. Passing this probe is not overall experience acceptance.
"""

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx


async def run(url: str, output: Path, *, dependency: bool):
    report = {
        "kind": "native-page-dependency-chat" if dependency else "native-page-chat",
        "acceptance_complete": False,
        "turns": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    prompts = (
        [
            "为当前 Page 做一个金额汇总页面：用户输入逗号分隔的非负金额，点击计算后显示数量与合计。"
            "计算逻辑放在一个新建的 Function，页面通过命名绑定调用它。Function 接受 amounts 数字数组，"
            "空数组返回 count=0、total=0，负数和非数字报错；返回 count 和 total。"
            "在这个对话中创建依赖并保存两份草稿，执行可用的检查；不要发布或查询数据库。"
            "用中文简短说明完成的部分和实际未验证项。",
            "现在只解释页面和 Function 哪些检查真的执行了、哪些没执行；两句话内，不调用工具。",
        ]
        if dependency
        else [
            "为当前 Page 做一个订单列表演示：三条固定示例订单 A001 金额100、A002 金额200、"
            "A003 金额300，明确标注为示例数据。提供订单号搜索和当前可见订单金额合计。"
            "使用 main.tsx 和独立 CSS 文件，界面文字为中文。仅保存草稿并执行可用的检查，"
            "不要发布，不创建 Function，也不要查询数据库。用中文简短说明结果和未验证项。",
            "保留现有要求，再加一个最低金额筛选，与订单号搜索同时生效；没匹配时显示“没有匹配订单”，"
            "合计为0。更新草稿并检查，仍不要发布。",
            "现在只解释哪些检查真的执行了，哪些没有；两句话内，不再改代码或调用工具。",
        ]
    )
    try:
        async with httpx.AsyncClient(base_url=url, timeout=180, trust_env=False) as client:
            health = await client.get("/api/v1/schedules/worker-health")
            health.raise_for_status()
            assert health.json()["autostart"] is False
            response = await client.post("/api/v1/pages", json={"name": report["kind"]})
            response.raise_for_status()
            report["page"] = response.json()
            page_id = report["page"]["id"]
            response = await client.post(
                "/api/v1/conversations",
                json={
                    "title": "Page 原生构建自测",
                    "scene": {"page_ids": [page_id], "datasource_ids": []},
                },
            )
            response.raise_for_status()
            report["conversation_id"] = response.json()["id"]
            for index, prompt in enumerate(prompts):
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
                            raise AssertionError(
                                "Draft-only request unexpectedly required approval"
                            )
                for key, endpoint in (
                    ("run", f"/api/v1/runs/{run_id}"),
                    ("draft", f"/api/v1/pages/{page_id}/draft"),
                ):
                    response = await client.get(endpoint)
                    response.raise_for_status()
                    turn[key] = response.json()
                turn["functions"] = []
                for owned in turn["draft"]["owned_functions"]:
                    response = await client.get(f"/api/v1/functions/{owned['id']}/draft")
                    response.raise_for_status()
                    turn["functions"].append(response.json())
                turn["seconds"] = time.monotonic() - started
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
                print(
                    json.dumps(
                        {
                            "turn": index + 1,
                            "seconds": turn["seconds"],
                            "status": turn["run"]["status"],
                            "text": turn["visible_text"],
                            "checks": turn["draft"]["validation"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                assert turn["run"]["status"] == "finished"
                assert turn["draft"]["current_release_id"] is None
                assert turn["draft"]["revision_id"]
                if index == len(prompts) - 1:
                    assert not turn["run"]["tool_calls"]
                    assert (
                        turn["draft"]["revision_id"] == report["turns"][-2]["draft"]["revision_id"]
                    )
                else:
                    checks = turn["draft"]["validation"]
                    assert checks, "No Page validation record"
                    assert any(
                        check["name"] == "source_compile"
                        and check["executed"]
                        and check["status"] == "passed"
                        for check in checks["checks"]
                    )
                    response = await client.get(
                        f"/api/v1/pages/{page_id}/preview",
                        params={"revision_id": turn["draft"]["revision_id"]},
                    )
                    response.raise_for_status()
                    assert response.json()["html"]
                    if dependency:
                        assert turn["draft"]["bindings"] and turn["functions"]
                        assert all(
                            item["revision_id"]
                            and item["current_release_id"] is None
                            and item["validation"]
                            for item in turn["functions"]
                        )
            if not dependency:
                assert (
                    report["turns"][0]["draft"]["revision_id"]
                    != report["turns"][1]["draft"]["revision_id"]
                )
            report["integration_checks_passed"] = True
    except BaseException as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dependency", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.url, args.output, dependency=args.dependency))
