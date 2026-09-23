"""Real Chat API retrieval probe using isolated synthetic private documents.

No model replacement, hidden retrieval model, answer rewriting or generated replies.
The fixture provides source data; model tools must retrieve it through the product.
"""

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx


async def run(url: str, output: Path):
    report = {"kind": "native-knowledge-chat", "acceptance_complete": False, "turns": []}
    output.parent.mkdir(parents=True, exist_ok=True)
    original = "# 星港任务重试策略\n\nTEMP_BUSY 表示执行槽位暂时占满。\n最多尝试 4 次，包含首次尝试。\n相邻尝试之间等待 11 秒。\n达到上限后标记 pending_review，不自动重新派发。\n"
    updated = "# 星港任务重试策略更新\n\n本文替代 retry-policy.md 中的 TEMP_BUSY 重试规则。\n最多尝试 2 次，包含首次尝试。\n相邻尝试之间等待 19 秒。\n达到上限后标记 manual_review，不自动重新派发。\n本文没有规定网络错误或其他错误的重试策略。\n"
    prompts = [
        "只依据当前授权知识库，告诉我星港任务遇到 TEMP_BUSY 时最多尝试几次、间隔多久、达到上限怎么处理。给出文档文件名和行号，简短中文，不访问数据库。",
        "所以第一次也包含在刚才的上限里，对吗？两句话内回答，不要再调用工具。",
        "我刚上传了 retry-update.md，请以这份更新文档为准重新说明 TEMP_BUSY 的规则。另告诉我网络错误是否也适用这个策略；没写的不要猜。给出来源，简短中文，不访问数据库。",
    ]
    try:
        async with httpx.AsyncClient(base_url=url, timeout=180, trust_env=False) as client:
            health = await client.get("/api/v1/schedules/worker-health")
            health.raise_for_status()
            assert health.json()["autostart"] is False
            response = await client.post(
                "/api/v1/knowledge-bases",
                json={"name": "原生知识检索自测", "description": "隔离测试资料，不是真实业务策略"},
            )
            response.raise_for_status()
            kb_id = response.json()["id"]
            report["knowledge_base_id"] = kb_id
            response = await client.post(
                f"/api/v1/knowledge-bases/{kb_id}/documents",
                files={"file": ("retry-policy.md", original, "text/markdown")},
            )
            response.raise_for_status()
            report["source_document"] = response.json()
            response = await client.post(
                "/api/v1/conversations",
                json={
                    "title": "知识检索连续对话自测",
                    "scene": {"knowledge_base_ids": [kb_id], "datasource_ids": []},
                },
            )
            response.raise_for_status()
            conversation_id = response.json()["id"]
            report["conversation_id"] = conversation_id
            for index, prompt in enumerate(prompts):
                if index == 2:
                    response = await client.post(
                        f"/api/v1/knowledge-bases/{kb_id}/documents",
                        files={"file": ("retry-update.md", updated, "text/markdown")},
                    )
                    response.raise_for_status()
                    report["updated_document"] = response.json()
                started = time.monotonic()
                response = await client.post(
                    f"/api/v1/conversations/{conversation_id}/runs",
                    json={"client_request_id": uuid.uuid4().hex, "prompt": prompt},
                )
                response.raise_for_status()
                run_id = response.json()["id"]
                turn = {"run_id": run_id, "prompt": prompt, "events": [], "visible_text": ""}
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
                                "Read-only retrieval unexpectedly paused for approval"
                            )
                response = await client.get(f"/api/v1/runs/{run_id}")
                response.raise_for_status()
                state = response.json()
                turn.update(state=state, seconds=time.monotonic() - started)
                assert state["status"] == "finished", state["status"]
                assert state["approvals"] == []
                calls = state["tool_calls"]
                assert all(call["name"].startswith("knowledge_") for call in calls)
                if index == 1:
                    assert not calls, "Follow-up explicitly requested no tools"
                else:
                    source_file = "retry-policy.md" if index == 0 else "retry-update.md"
                    expected = "11" if index == 0 else "19"
                    assert expected in turn["visible_text"]
                    assert source_file in turn["visible_text"]
                    # Evidence may come from full-text search or a direct read;
                    # do not prescribe the model's tool order.
                    evidence = [
                        call["result"]["content"]
                        for call in calls
                        if call.get("result")
                        and call["result"]["outcome"] == "success"
                        and call["name"] in {"knowledge_read", "knowledge_search"}
                    ]
                    assert any(
                        source_file in json.dumps(item) and expected in json.dumps(item)
                        for item in evidence
                    )
                print(
                    json.dumps(
                        {
                            "turn": index + 1,
                            "seconds": round(turn["seconds"], 3),
                            "tools": [call["name"] for call in calls],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            report["integration_passed"] = True
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
        raise
    finally:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8011")
    parser.add_argument(
        "--output", type=Path, default=Path("tmp/agent-runtime-knowledge-smoke.json")
    )
    args = parser.parse_args()
    asyncio.run(run(args.url, args.output))
