"""Real Chat API context continuity smoke; run against an explicit isolated service.

Use a deliberately small configured window (8192 tokens) to exercise compaction.
That test policy is not a claim about the provider's actual capacity. All turns
are submitted through HTTP; no native history, summary or model answer is seeded.
"""

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx


def prompts():
    yield "这是一段合成材料的连续分析测试。原始目标：分析海棠服务延迟，预算上限37元。只使用我给的资料，不调用任何工具，不执行修改。接下来逐批给材料，每次只简短确认；最后我会要求结论。"
    facts = [
        "关键观测：海棠服务原始 p95 为380毫秒。唯一待核实事项是统计窗口是否包含冷启动；目前没有核实，不要声称已验证。",
        "补充观测：客户端存在重复重试，但没有次数统计，不能推断影响比例。",
        "环境信息：这是合成离线样本，不是线上监控。",
        "备忘：这次只对比给出的数据，不把相关性当作因果。",
        "口径说明：中位数没有提供，不要编造。",
        "范围说明：没有任何获准的数据库写入。",
        "本批不增加任何新的性能结论。",
    ]
    for batch, fact in enumerate(facts, 1):
        # Distinct reference rows, not product-side special cases or fixed answers.
        rows = "\n".join(
            f"样本 {batch}-{i:02d}：这是用于核对材料完整性的离线登记；字段仅为记录标识，不含耗时、成功率或其他性能指标，不能据此计算性能。"
            for i in range(24)
        )
        yield f"第{batch}批。{fact}\n以下登记是背景材料，只需确认已收到。\n{rows}"
    yield "更正：服务现在叫银杏，原来的海棠是旧称；p95应为420毫秒，380是录入错误。请只用两句话总结最新服务名称、p95、原始预算上限和最早那个待核实事项，明确目前是否核实。不要列计划、引用登记行或调用工具。"


async def run(url: str, output: Path):
    report = {
        "kind": "real-http-context-smoke",
        "url": url,
        "acceptance_complete": False,
        "turns": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(base_url=url, timeout=180, trust_env=False) as client:
            created = await client.post(
                "/api/v1/conversations", json={"title": "上下文连续性实际测试"}
            )
            created.raise_for_status()
            conversation = report["conversation_id"] = created.json()["id"]
            for prompt in prompts():
                start = time.monotonic()
                response = await client.post(
                    f"/api/v1/conversations/{conversation}/runs",
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
                        event["received_seconds"] = time.monotonic() - start
                        turn["events"].append(event)
                        if event["type"] == "assistant_delta":
                            turn["visible_text"] += event["text"]
                        if event["type"] == "run_paused":
                            raise AssertionError("No action was requested or authorized")
                state = await client.get(f"/api/v1/runs/{run_id}")
                state.raise_for_status()
                turn["state"] = state.json()
                turn["seconds"] = time.monotonic() - start
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
                print(
                    json.dumps(
                        {
                            "turn": len(report["turns"]),
                            "status": turn["state"]["status"],
                            "text": turn["visible_text"],
                            "compactions": [
                                e for e in turn["events"] if e["type"] == "context_compacted"
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                assert turn["state"]["status"] == "finished", turn["state"]["error_code"]
                assert not turn["state"]["tool_calls"]
            compactions = [
                e for t in report["turns"] for e in t["events"] if e["type"] == "context_compacted"
            ]
            assert any(e["mode"] == "summary" for e in compactions), "No usable real model summary"
            assert any(e.get("base_snapshot_id") and e["mode"] == "summary" for e in compactions), (
                "Incremental summary reuse was not exercised"
            )
            answer = report["turns"][-1]["visible_text"]
            for fact in ("银杏", "420", "37", "冷启动"):
                assert fact in answer, f"Missing continuity fact: {fact}"
            report["integration_checks_passed"] = True
    finally:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Integration checks passed. Human expression review still required: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, default=Path("tmp/agent-runtime-context-smoke.json"))
    args = parser.parse_args()
    asyncio.run(run(args.url, args.output))
