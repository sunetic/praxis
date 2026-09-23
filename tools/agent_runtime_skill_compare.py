"""Same-model native Agent / real Chat HTTP comparison in a fresh isolated workspace.

Both paths execute the actual draft tools. The minimal path omits product context
management, durable dispatch and SSE projection. Only the HTTP transport is observed;
no response, instruction, tool argument or model setting is rewritten. Reports contain
synthetic conversation bodies, never authentication headers. This is an offline probe,
not a product fallback, quality judge or acceptance suite.

Supply the saved snapshot's encryption key through the environment before launch.
"""

import argparse
import asyncio
import json
import os
import socket
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path

PROMPTS = (
    "请创建并保存一个 Skill 草稿，名称为 harness-review，版本1.0.0，适用 PostgreSQL，"
    "不要设为 always_apply，也不要安装。用途：阅读用户提供的查询日志，区分事实和推断，"
    "证据不足时说明缺什么；不得自行运行SQL、命令或修改数据库。写中文说明和正文，"
    "内容简洁可复用，不编造具体日志。",
    "只把适用数据库改成 MySQL，其他内容保持不变，保存草稿。回答一句话。",
    "解释当前这份 Skill 何时适用，不要修改草稿，也不要安装。两句话即可。",
)


def outcome(turns):
    """Business facts only. Human expression/honesty review remains separate."""
    if len(turns) != 3 or any(
        "draft" not in turn or turn.get("status") != "finished" or "error_type" in turn
        for turn in turns
    ):
        return {"complete": False}
    first, second, third = [turn["draft"] for turn in turns]
    return {
        "complete": True,
        "created": (
            first["content"]["name"] == "harness-review"
            and first["content"]["database"] == "postgresql"
            and first["content"]["version"] == "1.0.0"
            and first["content"]["always_apply"] is False
            and bool(first["content"]["prompt"].strip())
        ),
        "only_database_changed": second["content"] == {**first["content"], "database": "mysql"},
        "explanation_did_not_edit": third == second,
    }


def request_parity(samples):
    """Only fresh resource identity differs; do not normalize model instructions."""
    pairs = {}
    for sample in samples:
        if not sample["turns"] or not sample["turns"][0].get("http"):
            continue
        request = sample["turns"][0]["http"][0]["request"]
        normalized = json.loads(json.dumps(request).replace(sample["draft_id"], "DRAFT_ID"))
        pairs.setdefault(sample["sample"], {})[sample["mode"]] = normalized
    return [
        {
            "sample": number,
            "ignored": "fresh draft_id only",
            "first_requests_equal": pair["minimal"] == pair["http"],
            "different_fields": sorted(
                key
                for key in pair["minimal"].keys() | pair["http"].keys()
                if pair["minimal"].get(key) != pair["http"].get(key)
            ),
        }
        for number, pair in pairs.items()
        if set(pair) == {"minimal", "http"}
    ]


async def run(args):
    workspace = Path(tempfile.mkdtemp(prefix="praxis-skill-compare-"))
    os.environ.update(
        DATA_DIR=str(workspace),
        DATABASE_URL=f"sqlite:///{workspace / 'http.db'}",
        DEBUG="false",
        TRACING_ENABLED="false",
        SCHEDULER_AUTOSTART="false",
        PRAXIS_DEMO_BOOTSTRAP="false",
        PYDANTIC_AI_NO_BANNER="1",
    )
    # Application imports follow isolation: module-level settings/engines must
    # never resolve the user's ordinary database or installed Skill directory.
    import httpx
    import httpx2
    import uvicorn
    from openai import AsyncOpenAI
    from pydantic_ai import Agent, DeferredToolRequests
    from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.usage import UsageLimits
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    from app.db.database import SessionLocal
    from app.main import agent_runtime, app
    from app.services.agent.application import RuntimeApplication
    from app.services.agent.definitions import RunDependencies
    from app.services.agent.models import ModelFactory, ModelSnapshot
    from app.services.agent.persistence import run_db
    from app.services.agent.runtime import ExecutionBudget
    from app.services.agent.store import RunStore
    from app.services.platform.settings_store import upsert_setting
    from app.services.skill.native_authoring import SkillDraftStore
    from tools.agent_runtime_wire_probe import ObservedTransport, wire_summary

    with sqlite3.connect(f"file:{args.snapshot_db}?mode=ro", uri=True) as db:
        row = db.execute(
            "select model_snapshot from agent_runs where model_snapshot is not null "
            "order by created_at desc limit 1"
        ).fetchone()
    config = ModelSnapshot.model_validate(json.loads(row[0])).restore()
    observed = []

    class ObservedFactory(ModelFactory):
        def _build(self, connection):
            client = AsyncOpenAI(
                api_key=connection.api_key.get_secret_value(),
                base_url=str(connection.base_url),
                timeout=connection.timeout_seconds,
                max_retries=connection.transport_retries,
                http_client=httpx2.AsyncClient(transport=ObservedTransport(observed)),
            )
            self._clients.add(client)
            model = OpenAIChatModel(
                connection.model_name,
                provider=OpenAIProvider(openai_client=client),
            )
            self._models[connection] = model
            return model

    factory = ObservedFactory()
    await agent_runtime.models.close()
    agent_runtime.models = factory
    agent_runtime.service.model_factory = factory.get_model
    agent_runtime.service.model_snapshot_factory = factory.snapshot
    minimal_engine = create_engine(
        f"sqlite:///{workspace / 'minimal.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(minimal_engine)
    minimal = RuntimeApplication(sessions=sessionmaker(minimal_engine), models=factory)
    minimal.store = RunStore(minimal.sessions, lease_seconds=900)
    drafts = SkillDraftStore(minimal.sessions)
    report = {
        "kind": "same-model-native-vs-http",
        "workspace": str(workspace),
        "configuration": config.model_dump(mode="json"),
        "samples": [],
        "acceptance_complete": False,
    }

    def save():
        report["request_parity"] = request_parity(report["samples"])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2).replace(
                config.api_key.get_secret_value(), "[redacted]"
            )
        )

    async def drain(_ctx, events):
        async for _event in events:
            pass

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(30):
            while not server.started:
                if server_task.done():
                    await server_task
                    raise RuntimeError("Isolated HTTP service stopped during startup")
                await asyncio.sleep(0.05)
        report["http_url"] = url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        print(json.dumps({"workspace": str(workspace), "http_url": url}), flush=True)
        async with httpx.AsyncClient(base_url=url, timeout=200, trust_env=False) as client:
            response = await client.patch(
                "/api/v1/settings",
                json={
                    "ai_model": config.model_name,
                    "ai_base_url": str(config.base_url),
                    "ai_api_key": config.api_key.get_secret_value(),
                    "context_window_tokens": config.context_window_tokens,
                    "context_compression_threshold_percent": config.context_compression_threshold_percent,
                },
            )
            response.raise_for_status()
            # These existing settings do not yet have public API fields. Preserve
            # the source configuration exactly, without changing the source DB.
            with SessionLocal.begin() as db:
                for key, value in {
                    "ai_max_output_tokens": config.max_output_tokens,
                    "ai_summary_output_tokens": config.summary_output_tokens,
                    "ai_temperature": config.temperature,
                    "ai_top_p": config.top_p,
                }.items():
                    if value is not None:
                        upsert_setting(db, key, value)
            if await run_db(factory.config_loader) != config:
                raise ValueError(
                    "Isolated platform settings differ from the comparison configuration"
                )
            for repetition in range(args.samples):
                # Alternate order to avoid always giving one path the cold start.
                modes = ("minimal", "http") if repetition % 2 == 0 else ("http", "minimal")
                for mode in modes:
                    sample = {"sample": repetition + 1, "mode": mode, "turns": []}
                    report["samples"].append(sample)
                    history = []
                    if mode == "http":
                        response = await client.post("/api/v1/skill-drafts")
                        response.raise_for_status()
                        draft = response.json()
                    else:
                        draft = drafts.create("local")
                    scene = {
                        "skill_draft_ids": [draft["id"]],
                        "datasource_ids": [],
                        "knowledge_base_ids": [],
                        "service_ids": [],
                    }
                    sample["draft_id"] = draft["id"]
                    if mode == "http":
                        response = await client.post("/api/v1/conversations", json={"scene": scene})
                        response.raise_for_status()
                        conversation_id = response.json()["id"]
                    else:
                        conversation_id = uuid.uuid4().hex
                        minimal.store.create_conversation(conversation_id, "local", scene=scene)
                    for prompt in PROMPTS:
                        turn = {"prompt": prompt}
                        sample["turns"].append(turn)
                        offset, started = len(observed), time.monotonic()
                        try:
                            async with asyncio.timeout(180):
                                if mode == "http":
                                    response = await client.post(
                                        f"/api/v1/conversations/{conversation_id}/runs",
                                        json={
                                            "prompt": prompt,
                                            "client_request_id": uuid.uuid4().hex,
                                        },
                                    )
                                    response.raise_for_status()
                                    turn["run_id"] = run_id = response.json()["id"]
                                    turn["events"], turn["visible_text"] = [], ""
                                    async with client.stream(
                                        "GET", f"/api/v1/runs/{run_id}/events"
                                    ) as stream:
                                        stream.raise_for_status()
                                        async for line in stream.aiter_lines():
                                            if line.startswith("data:"):
                                                event = json.loads(line[5:])
                                                turn["events"].append(event)
                                                if event["type"] == "assistant_delta":
                                                    turn["visible_text"] += event["text"]
                                                if event["type"] == "run_paused":
                                                    await client.post(
                                                        f"/api/v1/runs/{run_id}/cancel"
                                                    )
                                                    raise RuntimeError(
                                                        "Unexpected approval in draft-only task"
                                                    )
                                    response = await client.get(f"/api/v1/runs/{run_id}")
                                    response.raise_for_status()
                                    turn["status"] = response.json()["status"]
                                    response = await client.get(
                                        f"/api/v1/skill-drafts/{draft['id']}"
                                    )
                                    response.raise_for_status()
                                    turn["draft"] = response.json()
                                else:
                                    definition = minimal.resolve(conversation_id, "local", {})
                                    # Bookends only: persist a legitimate run reference for the
                                    # real draft store, without starting the product dispatcher.
                                    row = minimal.store.submit(
                                        conversation_id,
                                        "local",
                                        uuid.uuid4().hex,
                                        prompt,
                                        definition,
                                    )
                                    turn["run_id"] = row["id"]
                                    owner = uuid.uuid4().hex
                                    minimal.store.claim(row["id"], owner)
                                    agent = Agent(
                                        await factory.get_model(),
                                        name=definition.name,
                                        deps_type=RunDependencies,
                                        instructions=definition.instructions,
                                        tools=[
                                            minimal.tools[name].tool
                                            for name in sorted(definition.tool_names)
                                        ],
                                        output_type=[str, DeferredToolRequests],
                                        end_strategy="graceful",
                                        retries=2,
                                        model_settings=config.request_settings(),
                                    )
                                    result = await agent.run(
                                        prompt,
                                        message_history=history,
                                        deps=RunDependencies(
                                            run_id=row["id"],
                                            conversation_id=conversation_id,
                                            actor_id="local",
                                            scope=definition.scope,
                                        ),
                                        event_stream_handler=drain,
                                        usage_limits=UsageLimits(request_limit=50),
                                    )
                                    turn["messages"] = json.loads(
                                        ModelMessagesTypeAdapter.dump_json(result.new_messages())
                                    )
                                    turn["visible_text"] = "".join(
                                        part.content
                                        for message in result.new_messages()
                                        if isinstance(message, ModelResponse)
                                        for part in message.parts
                                        if isinstance(part, TextPart)
                                    )
                                    history = result.all_messages()
                                    minimal.store.finish(
                                        row["id"],
                                        owner,
                                        status="finished",
                                        history=history,
                                        budget=ExecutionBudget(usage=result.usage),
                                        output=result.output,
                                    )
                                    turn["status"] = "finished"
                                    turn["draft"] = drafts.read(draft["id"], "local")
                        except Exception as exc:
                            turn["error_type"] = type(exc).__name__
                        finally:
                            turn["seconds"] = time.monotonic() - started
                            turn["http"] = observed[offset:]
                            for attempt in turn["http"]:
                                attempt["summary"] = wire_summary(attempt.get("wire_body", ""))
                            turn["wire_content_equals_visible"] = "".join(
                                attempt["summary"]["content"] for attempt in turn["http"]
                            ) == turn.get("visible_text")
                            save()
                            print(
                                json.dumps(
                                    {
                                        "sample": repetition + 1,
                                        "mode": mode,
                                        "turn": len(sample["turns"]),
                                        "seconds": turn["seconds"],
                                        "error": turn.get("error_type"),
                                        "text": turn.get("visible_text"),
                                        "wire_content_equals_visible": turn[
                                            "wire_content_equals_visible"
                                        ],
                                    },
                                    ensure_ascii=False,
                                ),
                                flush=True,
                            )
                        if "error_type" in turn:
                            break
                    sample["business_facts"] = outcome(sample["turns"])
                    save()
    finally:
        server.should_exit = True
        await server_task
        await factory.close()
        minimal_engine.dispose()
        listener.close()
        save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-db", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
