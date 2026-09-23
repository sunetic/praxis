"""Kill an isolated product process after real MySQL commit, before reply delivery.

Uses a completed database_write_smoke fixture, never the original project DB.
A packet relay withholds the real EXECUTE completion packet; model/tool results
are not synthesized. The unknown call and its fence are retained for reconciliation.
"""

import argparse
import asyncio
import json
import os
import socket
import sqlite3
import sys
import time
import uuid
from pathlib import Path


class CommitReplyRelay:
    def __init__(self, upstream_port):
        self.upstream_port = upstream_port
        self.committed_reply = asyncio.Event()
        self.release = asyncio.Event()
        self.dispatches = 0
        self.reply_kind = None
        self.tasks = set()

    async def handle(self, reader, writer):
        current = asyncio.current_task()
        self.tasks.add(current)
        upstream_writer = None
        pending = []
        executing = False

        async def copy(source, destination, from_client):
            nonlocal executing
            while True:
                header = await source.readexactly(4)
                body = await source.readexactly(int.from_bytes(header[:3], "little"))
                if from_client and body == b"\x03EXECUTE praxis_approved":
                    executing = True
                    self.dispatches += 1
                elif not from_client and executing:
                    self.reply_kind = body[0]
                    self.committed_reply.set()
                    # The product has sent the SQL, but cannot observe this reply.
                    await self.release.wait()
                    return
                destination.write(header + body)
                await destination.drain()

        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                "127.0.0.1", self.upstream_port
            )
            pending = [
                asyncio.create_task(copy(reader, upstream_writer, True)),
                asyncio.create_task(copy(upstream_reader, writer, False)),
            ]
            await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            writer.close()
            if upstream_writer is not None:
                upstream_writer.close()
            self.tasks.discard(current)

    async def close(self):
        self.release.set()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run(args):
    import aiomysql
    import httpx
    from sqlglot import parse_one

    prior = json.loads(args.fixture_report.read_text())
    workspace = Path(prior["workspace"]).resolve()
    fixture = prior["fixture"]
    assert workspace.parent == Path("/tmp") and workspace.name.startswith("praxis-native-db-write-")
    assert fixture["host"] == "127.0.0.1" and fixture["database"].startswith("praxis_agent_write_")
    os.environ.update(
        DATA_DIR=str(workspace),
        DATABASE_URL=f"sqlite:///{workspace / 'runtime.db'}",
        DEBUG="false",
        TRACING_ENABLED="false",
        SCHEDULER_AUTOSTART="false",
        PRAXIS_DEMO_BOOTSTRAP="false",
    )
    from app.core.security import decrypt_secret

    with sqlite3.connect(f"file:{workspace / 'runtime.db'}?mode=ro", uri=True) as db:
        record = db.execute(
            "select id,user,password,database,host,port from datasources where database=?",
            (fixture["database"],),
        ).fetchone()
        assert (
            record
            and record[1] == fixture["user"]
            and record[4:] == (fixture["host"], fixture["port"])
        )
        assert not db.execute(
            "select id from agent_runs where status in ('queued','running','waiting_approval')"
        ).fetchall()
    source_id, username, encrypted_password, database, _, mysql_port = record
    password = decrypt_secret(encrypted_password)
    report = {
        "kind": "real-model-mysql-process-crash",
        "workspace": str(workspace),
        "fixture": fixture,
        "acceptance_complete": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    log = (workspace / "crash-server.log").open("a")
    process, reader, server = None, None, None
    relay = CommitReplyRelay(mysql_port)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = report["url"] = f"http://127.0.0.1:{port}"

    async def start():
        nonlocal process
        process = await asyncio.create_subprocess_exec(
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
        async with httpx.AsyncClient(base_url=url, timeout=2, trust_env=False) as client:
            async with asyncio.timeout(30):
                while True:
                    assert process.returncode is None, "Isolated service exited"
                    try:
                        if (await client.get("/api/v1/settings")).status_code == 200:
                            return
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)

    async def request(client, method, path, **kwargs):
        response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    async def value():
        async with reader.cursor() as cursor:
            await cursor.execute(f"SELECT `{column}` FROM counters WHERE id=%s", (args.row_id,))
            row = await cursor.fetchone()
            assert row is not None
            return row[0]

    try:
        reader = await aiomysql.connect(
            host="127.0.0.1",
            port=mysql_port,
            user=username,
            password=password,
            db=database,
            autocommit=True,
        )
        async with reader.cursor() as cursor:
            await cursor.execute("SHOW COLUMNS FROM counters")
            columns = [row[0] for row in await cursor.fetchall()]
            column = next(name for name in ("n", "live_count") if name in columns)
        before = report["before"] = await value()
        server = await asyncio.start_server(relay.handle, "127.0.0.1", 0)
        proxy_port = server.sockets[0].getsockname()[1]
        report["relay_port"] = proxy_port
        await start()
        async with httpx.AsyncClient(base_url=url, timeout=180, trust_env=False) as client:
            await request(
                client, "PATCH", f"/api/v1/datasources/{source_id}", json={"port": proxy_port}
            )
            conversation = await request(
                client,
                "POST",
                "/api/v1/conversations",
                json={
                    "title": "写入提交后进程中断",
                    "scene": {
                        "datasource_ids": [source_id],
                        "knowledge_base_ids": [],
                        "service_ids": [],
                    },
                },
            )
            sql = f"UPDATE counters SET {column} = {column} + 1 WHERE id = {args.row_id}"
            report["sql"] = sql
            row = await request(
                client,
                "POST",
                f"/api/v1/conversations/{conversation['id']}/runs",
                json={
                    "client_request_id": uuid.uuid4().hex,
                    "prompt": f"在当前隔离测试库执行这条 SQL，将指定记录加一：{sql}。请发起平台审批，然后用简短中文说明真实执行结果，不操作其他记录。",
                },
            )
            report["run_id"] = row["id"]
            async with asyncio.timeout(120):
                while row["status"] in {"queued", "running"}:
                    await asyncio.sleep(0.1)
                    row = await request(client, "GET", f"/api/v1/runs/{row['id']}")
            report["before_approval"] = row
            assert row["status"] == "waiting_approval"
            assert len(row["approvals"]) == 1
            approval = row["approvals"][0]
            call = next(c for c in row["tool_calls"] if c["call_id"] == approval["call_id"])
            assert (
                call["name"] == "request_database_change"
                and call["arguments"]["datasource_id"] == source_id
            )
            assert parse_one(call["arguments"]["sql"], read="mysql").sql(
                dialect="mysql", normalize=True, identify=True
            ) == parse_one(sql, read="mysql").sql(dialect="mysql", normalize=True, identify=True)
            assert await value() == before and relay.dispatches == 0
            approval_path = f"/api/v1/runs/{row['id']}/tool-calls/{approval['call_id']}/approval"
            decision = {"fingerprint": approval["fingerprint"], "approved": True}
            await request(client, "POST", approval_path, json=decision)
            await asyncio.wait_for(relay.committed_reply.wait(), 30)
            assert relay.reply_kind == 0, "Database did not acknowledge success"
            assert await value() == before + 1
            report["at_commit"] = await request(client, "GET", f"/api/v1/runs/{row['id']}")
            assert (
                next(
                    c for c in report["at_commit"]["tool_calls"] if c["call_id"] == call["call_id"]
                )["status"]
                == "executing"
            )
            report["killed_pid"] = process.pid
            process.kill()
            report["crash_exit_code"] = await process.wait()
            assert report["crash_exit_code"] == -9
            await start()
            started = time.monotonic()
            async with asyncio.timeout(60):
                while True:
                    row = await request(client, "GET", f"/api/v1/runs/{row['id']}")
                    if row["status"] == "interrupted":
                        break
                    await asyncio.sleep(0.2)
            report["recovery_seconds"] = time.monotonic() - started
            report["after_restart"] = row
            assert (
                next(c for c in row["tool_calls"] if c["call_id"] == call["call_id"])["status"]
                == "outcome_unknown"
            )
            await request(client, "POST", approval_path, json=decision)
            report["after_duplicate_approval"] = await request(
                client, "GET", f"/api/v1/runs/{row['id']}"
            )
            assert report["after_duplicate_approval"]["status"] == "interrupted"
            assert relay.dispatches == 1 and await value() == before + 1
            with sqlite3.connect(f"file:{workspace / 'runtime.db'}?mode=ro", uri=True) as db:
                report["resource_locks"] = db.execute(
                    "select resource_key,run_id,call_id from agent_resource_locks where run_id=?",
                    (row["id"],),
                ).fetchall()
                report["events"] = [
                    dict(seq=s, kind=k, payload=json.loads(p))
                    for s, k, p in db.execute(
                        "select seq,kind,payload from agent_run_events where run_id=? order by seq",
                        (row["id"],),
                    )
                ]
            assert len(report["resource_locks"]) == 1
            report["crash_boundary_passed"] = True
            print(
                json.dumps(
                    {
                        "run_id": row["id"],
                        "status": row["status"],
                        "dispatches": relay.dispatches,
                        "before": before,
                        "after": await value(),
                        "recovery_seconds": report["recovery_seconds"],
                    }
                ),
                flush=True,
            )
    except BaseException as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        if reader is not None:
            try:
                report["after"] = await asyncio.wait_for(value(), 5)
            except Exception as exc:
                report["final_observation_error"] = type(exc).__name__
            finally:
                reader.close()
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 15)
            except TimeoutError:
                process.kill()
                await process.wait()
        if server is not None:
            server.close()
            await server.wait_closed()
        await relay.close()
        log.close()
        report["dispatches"] = relay.dispatches
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2).replace(password, "[redacted]")
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-report", type=Path, required=True)
    parser.add_argument("--row-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
