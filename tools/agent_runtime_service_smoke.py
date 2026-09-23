"""Real model + product Chat API + an actual loopback HTTP service with isolated writes.

The loopback server is an explicitly labelled test environment, not production
business data. It rejects the wrong field and stores accepted notes. No model or
tool response is mocked. Approval decisions here are limited to those test notes.
"""

import argparse
import asyncio
import json
import socket
import time
import uuid
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


async def run(url: str, output: Path, repetitions: int):
    upstream = FastAPI()
    notes, attempts = [], []
    credential = uuid.uuid4().hex

    @upstream.post("/notes")
    async def create_note(request: Request):
        assert request.headers.get("authorization") == f"Bearer {credential}"
        body = await request.json()
        attempts.append({"method": "POST", "path": "/notes", "body": body})
        if not isinstance(body.get("label"), str) or not body["label"]:
            return JSONResponse(
                status_code=422,
                content={
                    "error": "missing_label",
                    "detail": "The required field is label, not name.",
                    "accepted_fields": {"label": "non-empty string"},
                    "created": False,
                },
            )
        note = {"id": uuid.uuid4().hex, "label": body["label"]}
        notes.append(note)
        return {"created": True, "note": note, "environment": "isolated HTTP test fixture"}

    @upstream.delete("/notes/{note_id}")
    async def delete_note(note_id: str):
        attempts.append({"method": "DELETE", "path": f"/notes/{note_id}"})
        return JSONResponse(
            status_code=500, content={"error": "Test must deny deletion before dispatch"}
        )

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    upstream_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(upstream, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    report = {
        "kind": "real-model-native-service-http",
        "product_url": url,
        "fixture_url": upstream_url,
        "acceptance_complete": False,
        "groups": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)

    async def checked(client, method, path, **kwargs):
        response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=url, timeout=180, trust_env=False) as client:
            for group_index in range(repetitions):
                label = f"联调记录-{uuid.uuid4().hex[:8]}"
                source = await checked(
                    client,
                    "POST",
                    "/api/v1/datasources",
                    json={
                        "name": f"HTTP联调元数据-{label}",
                        "db_type": "postgresql",
                        "host": "127.0.0.1",
                        "port": 9,
                        "user": "fixture",
                        "password": "",
                        "database": "fixture",
                        "cluster_key": f"http-fixture-{label}",
                        "attributes": {
                            "fixture": "metadata-only; database connection must not be used"
                        },
                    },
                )
                service = await checked(
                    client,
                    "POST",
                    "/api/v1/services",
                    json={
                        "name": f"隔离便签API-{label}",
                        "service_type": "http_api",
                        "resource_ref": f"datasource:{source['id']}",
                        "config": {
                            "base_url": upstream_url,
                            "auth_type": "bearer",
                            "response_format": "json",
                        },
                        "secrets": {"bearer_token": credential},
                    },
                )
                conversation = await checked(
                    client,
                    "POST",
                    "/api/v1/conversations",
                    json={
                        "title": "原生 Service 实际 HTTP 联调",
                        "scene": {"datasource_ids": [source["id"]]},
                    },
                )
                group = {
                    "conversation_id": conversation["id"],
                    "datasource_id": source["id"],
                    "service_id": service["id"],
                    "label": label,
                    "turns": [],
                }
                report["groups"].append(group)
                prompts = [
                    f"当前范围绑定了一个隔离测试用便签 HTTP 服务。请用 POST /notes 新建一条标题为“{label}”的便签，"
                    "标题放在 name 字段。若接口返回字段错误，请依据实际错误修正后继续。"
                    "允许为这条测试便签发起必要的审批，不操作其他资源。用简短中文告诉我实际结果和编号。",
                    "刚才最终保存的标题是什么？一句话回答，不调用工具。",
                    "请删除刚才创建的测试便签，接口是 DELETE /notes/{编号}。先让我审批。",
                ]
                baseline = len(notes)
                for index, prompt in enumerate(prompts):
                    started = time.monotonic()
                    row = await checked(
                        client,
                        "POST",
                        f"/api/v1/conversations/{conversation['id']}/runs",
                        json={
                            "client_request_id": uuid.uuid4().hex,
                            "prompt": prompt,
                        },
                    )
                    turn = {"run_id": row["id"], "prompt": prompt, "events": [], "decisions": []}
                    group["turns"].append(turn)
                    cursor = 0
                    async with asyncio.timeout(180):
                        while True:
                            async with client.stream(
                                "GET",
                                f"/api/v1/runs/{row['id']}/events",
                                headers={"Last-Event-ID": str(cursor)},
                            ) as stream:
                                stream.raise_for_status()
                                async for line in stream.aiter_lines():
                                    if line.startswith("data: "):
                                        event = json.loads(line[6:])
                                        event["received_seconds"] = time.monotonic() - started
                                        turn["events"].append(event)
                                        cursor = event["seq"]
                                        if event["type"] == "run_paused":
                                            break
                            row = await checked(client, "GET", f"/api/v1/runs/{row['id']}")
                            if row["status"] != "waiting_approval":
                                break
                            assert len(turn["decisions"]) < 6, "Unexpected repeated approvals"
                            pending = [
                                item for item in row["approvals"] if item["decision"] == "pending"
                            ]
                            assert pending, row
                            for approval in pending:
                                call = next(
                                    item
                                    for item in row["tool_calls"]
                                    if item["call_id"] == approval["call_id"]
                                )
                                arguments = call["arguments"]
                                assert (
                                    call["name"] == "call_service"
                                    and arguments["service_id"] == service["id"]
                                )
                                if index == 0:
                                    assert (
                                        arguments["method"] == "POST"
                                        and arguments["path"] == "/notes"
                                    )
                                    assert set(arguments.get("body", {}).values()) == {label}
                                    assert len(notes) == baseline, (
                                        "Write happened before approval or duplicate creation"
                                    )
                                else:
                                    assert index == 2 and arguments["method"] == "DELETE"
                                    assert arguments["path"] == f"/notes/{notes[-1]['id']}"
                                decision = {
                                    "fingerprint": approval["fingerprint"],
                                    "approved": index == 0,
                                }
                                approval_url = f"/api/v1/runs/{row['id']}/tool-calls/{approval['call_id']}/approval"
                                await checked(client, "POST", approval_url, json=decision)
                                await checked(client, "POST", approval_url, json=decision)
                                turn["decisions"].append(
                                    {"call_id": approval["call_id"], **decision}
                                )
                            # Observe this very run resuming; do not create a replacement.
                            while row["status"] == "waiting_approval":
                                await asyncio.sleep(0.02)
                                row = await checked(client, "GET", f"/api/v1/runs/{row['id']}")
                                if any(a["decision"] == "pending" for a in row["approvals"]):
                                    break
                    turn["state"] = row
                    turn["total_seconds"] = time.monotonic() - started
                    assert row["status"] == "finished", row
                    assert [event["seq"] for event in turn["events"]] == list(
                        range(1, row["event_seq"] + 1)
                    )
                    if index == 0:
                        assert len(notes) == baseline + 1 and notes[-1]["label"] == label
                        assert any(
                            call["status"] == "failed" and "missing_label" in str(call["result"])
                            for call in row["tool_calls"]
                        ), "Model did not exercise the failed-field path"
                    elif index == 1:
                        assert not row["tool_calls"]
                    else:
                        assert len(turn["decisions"]) == 1 and all(
                            not item["approved"] for item in turn["decisions"]
                        )
                        assert len(notes) == baseline + 1
                        assert not any(item["method"] == "DELETE" for item in attempts)
                    print(
                        json.dumps(
                            {
                                "group": group_index + 1,
                                "turn": index + 1,
                                "seconds": turn["total_seconds"],
                                "text": row["output"],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        report["fixture_notes"] = notes
        report["fixture_attempts"] = attempts
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        server.should_exit = True
        await task
        sock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8011")
    parser.add_argument("--output", type=Path, default=Path("tmp/agent-runtime-service-smoke.json"))
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.output, args.repetitions))
