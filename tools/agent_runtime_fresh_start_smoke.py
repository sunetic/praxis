"""Fresh product startup and real Chat/SSE; reuse configured model, not old schema.

Only a saved encrypted model configuration is read from the source (read-only).
All API writes and generated Skills stay in a new temporary workspace. This is
integration evidence, not the full conversation/latency acceptance suite.
"""

import argparse
import asyncio
import json
import os
import socket
import sqlite3
import tempfile
from pathlib import Path


async def run(args):
    workspace = Path(tempfile.mkdtemp(prefix="praxis-native-fresh-"))
    os.environ.update(
        DATA_DIR=str(workspace),
        DATABASE_URL=f"sqlite:///{workspace / 'runtime.db'}",
        DEBUG="false",
        TRACING_ENABLED="false",
        SCHEDULER_AUTOSTART="false",
        PRAXIS_DEMO_BOOTSTRAP="false",
        PYDANTIC_AI_NO_BANNER="1",
    )
    # Import only after setting the isolated database/data paths.
    import httpx
    import uvicorn
    from sqlalchemy import inspect, select

    from app.db.database import SessionLocal, engine
    from app.main import agent_runtime, app
    from app.models.agent_runs import RUNTIME_TABLES, runs
    from app.services.agent.models import ModelSnapshot
    from app.services.agent.persistence import run_db
    from app.services.platform.settings_store import upsert_setting
    from tools.agent_runtime_cold_start_smoke import run as cold_start
    from tools.agent_runtime_skill_smoke import run as skill_chat

    with sqlite3.connect(f"file:{args.snapshot_db.resolve()}?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT model_snapshot FROM agent_runs WHERE model_snapshot IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise ValueError("Source has no saved model configuration")
    config = ModelSnapshot.model_validate(json.loads(row[0])).restore()
    report = {
        "kind": "fresh-schema-real-chat",
        "workspace": str(workspace),
        "configuration": config.model_dump(mode="json"),
        "integration_passed": False,
        "acceptance_complete": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    url = report["url"] = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(30):
            while not server.started:
                if server_task.done():
                    await server_task
                    raise RuntimeError("Fresh service stopped during startup")
                await asyncio.sleep(0.05)
        names = report["tables"] = inspect(engine).get_table_names()
        retired = {
            "conversations",
            "messages",
            "conversation_context_snapshots",
            "chat_events",
            "pending_actions",
            "tool_executions",
            "build_sessions",
            "function_build_runs",
            "function_build_events",
        }
        assert not retired & set(names)
        assert {table.name for table in RUNTIME_TABLES} <= set(names)
        report["schema_passed"] = True
        async with httpx.AsyncClient(base_url=url, timeout=30, trust_env=False) as client:
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
            assert "ai_action_confirmation_bypass" not in response.json()
            response = await client.patch(
                "/api/v1/settings", json={"ai_action_confirmation_bypass": True}
            )
            assert response.status_code == 422
        with SessionLocal.begin() as db:
            for key, value in {
                "ai_max_output_tokens": config.max_output_tokens,
                "ai_summary_output_tokens": config.summary_output_tokens,
                "ai_temperature": config.temperature,
                "ai_top_p": config.top_p,
            }.items():
                if value is not None:
                    upsert_setting(db, key, value)
        assert await run_db(agent_runtime.models.config_loader) == config
        print(json.dumps({"workspace": str(workspace), "model": config.model_name}), flush=True)
        cold_output = args.output.with_name(args.output.stem + "-cold.json")
        skill_output = args.output.with_name(args.output.stem + "-skill.json")
        report["cold_report"] = str(cold_output)
        report["skill_report"] = str(skill_output)
        await cold_start(url, cold_output)
        report["cold_start_passed"] = True
        await skill_chat(url, skill_output)
        report["skill_chat_passed"] = True
        with SessionLocal() as db:
            statuses = db.execute(select(runs.c.status)).scalars().all()
        report["run_statuses"] = statuses
        assert statuses.count("finished") == 7
        assert statuses.count("cancelled") == 1
        assert len(statuses) == 8
        report["integration_passed"] = True
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "detail": str(exc)}
        raise
    finally:
        server.should_exit = True
        await server_task
        listener.close()
        report["service_stopped"] = True
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2).replace(
                config.api_key.get_secret_value(), "[redacted]"
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
