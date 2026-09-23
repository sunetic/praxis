"""Real Chat HTTP + configured model + a dedicated local MySQL database/account.

Only the named fixture increments are approved; deletes are denied. Before each
first approval, a real fixture column rename exercises recovery even if the model
inspects schema first. No model/tool response is replaced. The original
project/service/database is never reconfigured. Fixture data is retained for audit.
Set the snapshot encryption key and optional DEMO_MYSQL_ROOT_PASSWORD in the
environment. This integration probe is not the complete acceptance task set.
"""

import argparse
import asyncio
import json
import os
import socket
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path


async def run(args):
    import aiomysql
    import httpx
    from sqlglot import parse_one

    workspace = Path(tempfile.mkdtemp(prefix="praxis-native-db-write-"))
    os.environ.update(
        DATA_DIR=str(workspace),
        DATABASE_URL=f"sqlite:///{workspace / 'runtime.db'}",
        DEBUG="false",
        TRACING_ENABLED="false",
        SCHEDULER_AUTOSTART="false",
        PRAXIS_DEMO_BOOTSTRAP="false",
        PYDANTIC_AI_NO_BANNER="1",
    )
    from app.services.agent.models import ModelSnapshot

    with sqlite3.connect(f"file:{args.snapshot_db}?mode=ro", uri=True) as db:
        saved = db.execute(
            "select model_snapshot from agent_runs where model_snapshot is not null "
            "order by created_at desc limit 1"
        ).fetchone()
    config = ModelSnapshot.model_validate(json.loads(saved[0])).restore()
    database = "praxis_agent_write_" + uuid.uuid4().hex[:12]
    counter_column = "n"
    username, password = "agent_" + uuid.uuid4().hex[:12], uuid.uuid4().hex
    report = {
        "kind": "real-model-native-sql-write",
        "workspace": str(workspace),
        "configuration": config.model_dump(mode="json"),
        "fixture": {
            "database": database,
            "user": username,
            "host": "127.0.0.1",
            "port": args.mysql_port,
        },
        "groups": [],
        "acceptance_complete": False,
    }
    process, reader = None, None
    log = (workspace / "server.log").open("a")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        for secret in (password, config.api_key.get_secret_value()):
            encoded = encoded.replace(secret, "[redacted]")
        args.output.write_text(encoded)

    async def request(client, method, path, **kwargs):
        response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = report["url"] = f"http://127.0.0.1:{port}"

    async def start():
        nonlocal process
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
            stdout=log,
            stderr=log,
        )
        process = child  # Retain ownership even if readiness observation fails.
        async with httpx.AsyncClient(base_url=url, trust_env=False, timeout=2) as client:
            async with asyncio.timeout(30):
                while True:
                    if child.returncode is not None:
                        raise RuntimeError("Isolated product service exited before readiness")
                    try:
                        response = await client.get("/api/v1/settings")
                        if response.status_code == 200:
                            return child
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)

    async def stop(child):
        if child is not None and child.returncode is None:
            child.terminate()
            try:
                await asyncio.wait_for(child.wait(), 15)
            except TimeoutError:
                child.kill()
                await child.wait()

    async def counts():
        async with reader.cursor() as cursor:
            await cursor.execute(f"SELECT id,`{counter_column}` FROM counters ORDER BY id")
            return [list(row) for row in await cursor.fetchall()]

    def normalized(sql):
        return parse_one(sql, read="mysql").sql(dialect="mysql", normalize=True, identify=True)

    try:
        admin = await aiomysql.connect(
            host="127.0.0.1",
            port=args.mysql_port,
            user="root",
            password=os.environ.get("DEMO_MYSQL_ROOT_PASSWORD", "praxis-demo-root"),
            connect_timeout=5,
            autocommit=True,
        )
        try:
            async with admin.cursor() as cursor:
                # Names contain a fixed prefix and UUID hex only. Only this new
                # database/account is created; no existing object is overwritten.
                await cursor.execute(f"CREATE DATABASE `{database}`")
                await cursor.execute(f"CREATE USER '{username}'@'%%' IDENTIFIED BY %s", (password,))
                await cursor.execute(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP ON `{database}`.* TO '{username}'@'%'"
                )
                await cursor.execute(
                    f"CREATE TABLE `{database}`.counters(id INT PRIMARY KEY, n INT NOT NULL) ENGINE=InnoDB"
                )
                await cursor.executemany(
                    f"INSERT INTO `{database}`.counters VALUES(%s,0)",
                    [(i,) for i in range(1, args.samples + 1)],
                )
        finally:
            admin.close()
        reader = await aiomysql.connect(
            host="127.0.0.1",
            port=args.mysql_port,
            user=username,
            password=password,
            db=database,
            connect_timeout=5,
            autocommit=True,
        )
        report["before"] = await counts()
        process = await start()
        print(
            json.dumps({"workspace": str(workspace), "url": url, "fixture_database": database}),
            flush=True,
        )
        async with httpx.AsyncClient(base_url=url, timeout=180, trust_env=False) as client:
            await request(
                client,
                "PATCH",
                "/api/v1/settings",
                json={
                    "ai_model": config.model_name,
                    "ai_base_url": str(config.base_url),
                    "ai_api_key": config.api_key.get_secret_value(),
                    "sql_allow_mutating": True,
                    "context_window_tokens": config.context_window_tokens,
                    "context_compression_threshold_percent": config.context_compression_threshold_percent,
                },
            )
            # Preserve source sampling/output settings that lack public fields.
            from app.db.database import SessionLocal
            from app.services.platform.settings_store import upsert_setting

            with SessionLocal.begin() as db:
                for key, value in {
                    "ai_max_output_tokens": config.max_output_tokens,
                    "ai_summary_output_tokens": config.summary_output_tokens,
                    "ai_temperature": config.temperature,
                    "ai_top_p": config.top_p,
                }.items():
                    if value is not None:
                        upsert_setting(db, key, value)
            source = await request(
                client,
                "POST",
                "/api/v1/datasources",
                json={
                    "name": database,
                    "db_type": "mysql",
                    "host": "127.0.0.1",
                    "port": args.mysql_port,
                    "user": username,
                    "password": password,
                    "database": database,
                    "cluster_key": database,
                },
            )
            for index in range(1, args.samples + 1):
                conversation = await request(
                    client,
                    "POST",
                    "/api/v1/conversations",
                    json={
                        "title": f"数据库原生审批联调 {index}",
                        "scene": {
                            "datasource_ids": [source["id"]],
                            "knowledge_base_ids": [],
                            "service_ids": [],
                        },
                    },
                )
                group = {"id": index, "conversation_id": conversation["id"], "turns": []}
                report["groups"].append(group)
                prompts = [
                    f"这是隔离测试库，只操作 counters 表 id={index} 的记录。请先尝试 UPDATE counters SET counter = counter + 1 WHERE id = {index}。"
                    "如果字段不存在，请查看真实表结构，修正后继续把计数加一，并查询确认结果。必要的写入都先让我审批，中文简短说明实际结果。",
                    "刚才实际加了几次，现在的值是多少？一句话回答，不调用工具。",
                    f"请删除 counters 表 id={index} 的那条记录，先让我审批。",
                ]
                for turn_index, prompt in enumerate(prompts):
                    started = time.monotonic()
                    row = await request(
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
                    save()
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
                                    if line.startswith("data:"):
                                        event = json.loads(line[5:])
                                        turn["events"].append(event)
                                        cursor = event["seq"]
                                        if event["type"] == "run_paused":
                                            break
                            row = await request(client, "GET", f"/api/v1/runs/{row['id']}")
                            if row["status"] != "waiting_approval":
                                break
                            if index == 1 and turn_index == 0 and not turn["decisions"]:
                                # Real service restart while the original native
                                # call is awaiting approval, without creating a run.
                                original_calls = row["tool_calls"]
                                await stop(process)
                                process = await start()
                                row = await request(client, "GET", f"/api/v1/runs/{row['id']}")
                                assert row["status"] == "waiting_approval"
                                assert row["tool_calls"] == original_calls
                                turn["approval_survived_restart"] = True
                            assert len(turn["decisions"]) < 4, "Unexpected repeated approvals"
                            for approval in [
                                a for a in row["approvals"] if a["decision"] == "pending"
                            ]:
                                call = next(
                                    c
                                    for c in row["tool_calls"]
                                    if c["call_id"] == approval["call_id"]
                                )
                                assert call["name"] == "request_database_change"
                                assert call["arguments"]["datasource_id"] == source["id"]
                                sql = normalized(call["arguments"]["sql"])
                                observed = await counts()
                                if turn_index == 0:
                                    assert sql in {
                                        normalized(
                                            f"UPDATE counters SET {column} = {column} + 1 WHERE id = {index}"
                                        )
                                        for column in ("counter", "n", "live_count")
                                    }, "Unexpected write; not approved"
                                    assert dict(observed)[index] == 0, (
                                        "Write before approval or second increment"
                                    )
                                    if not turn["decisions"]:
                                        previous_column = counter_column
                                        counter_column = (
                                            "live_count" if counter_column == "n" else "n"
                                        )
                                        async with reader.cursor() as fixture_cursor:
                                            await fixture_cursor.execute(
                                                f"ALTER TABLE counters CHANGE COLUMN `{previous_column}` `{counter_column}` INT NOT NULL"
                                            )
                                        turn["fixture_schema_change"] = {
                                            "from": previous_column,
                                            "to": counter_column,
                                            "before_first_approval": True,
                                        }
                                else:
                                    assert turn_index == 2 and sql == normalized(
                                        f"DELETE FROM counters WHERE id = {index}"
                                    )
                                    assert dict(observed)[index] == 1
                                decision = {
                                    "fingerprint": approval["fingerprint"],
                                    "approved": turn_index == 0,
                                }
                                path = f"/api/v1/runs/{row['id']}/tool-calls/{approval['call_id']}/approval"
                                await request(client, "POST", path, json=decision)
                                await request(client, "POST", path, json=decision)
                                turn["decisions"].append(
                                    {"call_id": approval["call_id"], "before": observed, **decision}
                                )
                            save()
                            while row["status"] == "waiting_approval":
                                await asyncio.sleep(0.02)
                                row = await request(client, "GET", f"/api/v1/runs/{row['id']}")
                                if any(a["decision"] == "pending" for a in row["approvals"]):
                                    break
                    turn.update(
                        state=row, seconds=time.monotonic() - started, actual_rows=await counts()
                    )
                    save()
                    assert row["model"] == config.model_dump(mode="json")
                    assert row["status"] == "finished", row["error_code"]
                    assert [e["seq"] for e in turn["events"]] == list(
                        range(1, row["event_seq"] + 1)
                    )
                    assert dict(turn["actual_rows"])[index] == 1
                    if turn_index == 0:
                        writes = [
                            c for c in row["tool_calls"] if c["name"] == "request_database_change"
                        ]
                        assert sum(c["status"] == "succeeded" for c in writes) == 1
                        assert any(
                            c["status"] == "failed" and "Unknown column" in str(c["result"])
                            for c in writes
                        )
                        successful_write = next(c for c in writes if c["status"] == "succeeded")
                        write_seq = next(
                            e["seq"]
                            for e in turn["events"]
                            if e["type"] == "tool_result"
                            and e["call_id"] == successful_write["call_id"]
                            and e.get("outcome") == "success"
                        )
                        verified_calls = {
                            c["call_id"]
                            for c in row["tool_calls"]
                            if c["name"] == "query_database"
                            and c["status"] == "succeeded"
                            and "counters" in c["arguments"]["sql"].lower()
                            and any(
                                1 in result.values()
                                for result in c["result"]["content"].get("rows", [])
                            )
                        }
                        assert any(
                            e["type"] == "tool_result"
                            and e["call_id"] in verified_calls
                            and e["seq"] > write_seq
                            for e in turn["events"]
                        )
                    elif turn_index == 1:
                        assert not row["tool_calls"]
                    else:
                        # Preserve missing/duplicate approval as a failed result,
                        # but finish independent samples instead of selecting only
                        # the first successful dialogue. No repair/follow-up call.
                        turn["denial_flow_passed"] = (
                            len(turn["decisions"]) == 1 and not turn["decisions"][0]["approved"]
                        )
                        save()
                    print(
                        json.dumps(
                            {
                                "sample": index,
                                "turn": turn_index + 1,
                                "seconds": turn["seconds"],
                                "text": row["output"],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
            report["integration_passed"] = all(
                turn.get("denial_flow_passed", True)
                for group in report["groups"]
                for turn in group["turns"]
            )
    except BaseException as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        if reader is not None:
            try:
                report["after"] = await asyncio.wait_for(counts(), 5)
            except Exception as exc:
                report["final_observation_error"] = type(exc).__name__
            finally:
                reader.close()
        await stop(process)
        log.close()
        save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-db", type=Path, required=True)
    parser.add_argument("--mysql-port", type=int, default=3307)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
