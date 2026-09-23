"""Reconcile the retained real MySQL crash through the browser and resume its native run."""

import argparse
import asyncio
import json
import os
import socket
import sqlite3
import sys
from pathlib import Path


async def run(args):
    import aiomysql
    import httpx

    crash = json.loads(args.crash_report.read_text())
    workspace = Path(crash["workspace"]).resolve()
    fixture = crash["fixture"]
    assert workspace.parent == Path("/tmp") and workspace.name.startswith("praxis-native-db-write-")
    assert fixture["host"] == "127.0.0.1" and fixture["database"].startswith("praxis_agent_write_")
    assert crash["crash_boundary_passed"] and crash["dispatches"] == 1
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
        row = db.execute(
            "select id,user,password,host,port from datasources where database=?",
            (fixture["database"],),
        ).fetchone()
        assert row and row[1] == fixture["user"] and row[3] == fixture["host"]
        assert db.execute(
            "select status from agent_runs where id=?", (crash["run_id"],)
        ).fetchone() == ("interrupted",)
        assert not db.execute(
            "select id from agent_runs where status in ('running','queued','waiting_approval')"
        ).fetchall()
    source_id, username, encrypted, _, _ = row
    password = decrypt_secret(encrypted)
    report = {
        "kind": "real-crash-browser-reconciliation",
        "source_crash": str(args.crash_report),
        "workspace": str(workspace),
        "run_id": crash["run_id"],
        "acceptance_complete": False,
    }
    process = reader = None
    log = (workspace / "reconcile-server.log").open("a")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = report["url"] = f"http://127.0.0.1:{port}"

    async def values():
        async with reader.cursor() as cursor:
            await cursor.execute("SELECT id,n FROM counters ORDER BY id")
            return [list(row) for row in await cursor.fetchall()]

    try:
        reader = await aiomysql.connect(
            host=fixture["host"],
            port=fixture["port"],
            user=username,
            password=password,
            db=fixture["database"],
            autocommit=True,
        )
        report["before"] = await values()
        assert report["before"] == [[1, 1], [2, 1], [3, 0]]
        async with reader.cursor() as cursor:
            await cursor.execute("SHOW PROCESSLIST")
            processes = await cursor.fetchall()
            assert not [
                row for row in processes if row[1] == username and row[4] not in {"Sleep", "Query"}
            ]
            assert not [
                row
                for row in processes
                if row[1] == username and row[4] == "Query" and row[7] != "SHOW PROCESSLIST"
            ]
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
        async with httpx.AsyncClient(base_url=url, timeout=180, trust_env=False) as client:
            async with asyncio.timeout(30):
                while True:
                    assert process.returncode is None
                    try:
                        if (await client.get("/api/v1/settings")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
            onboarding = await client.post("/api/v1/onboarding/complete", json={})
            onboarding.raise_for_status()
            current = (await client.get(f"/api/v1/runs/{crash['run_id']}")).json()
            report["initial_run"] = current
            assert current["model"] == crash["after_restart"]["model"]
            assert current["model"]["model_name"] == "DeepSeek-V4-Flash-0731"
            evidence = (
                f"独立 MySQL 连接核对 {fixture['host']}:{fixture['port']}/{fixture['database']}："
                "counters 的 id=2、n=1，原值为0。原测试仅派发一次，数据库真实成功回执到达转发器后，"
                "原产品进程被 SIGKILL；转发器和旧连接均已结束，当前 PROCESSLIST 无遗留执行。"
                "本次只执行 SELECT 和 SHOW PROCESSLIST 核对，没有重放 UPDATE。"
            )
            browser = await asyncio.create_subprocess_exec(
                "npx",
                "playwright",
                "test",
                "e2e/live-reconciliation.spec.ts",
                "--output",
                str(args.browser_output.resolve()),
                cwd="frontend",
                env={
                    **os.environ,
                    "PRAXIS_LIVE_URL": url,
                    "PRAXIS_RECONCILE_RUN": crash["run_id"],
                    "PRAXIS_RECONCILE_EVIDENCE": evidence,
                    "PRAXIS_RECONCILE_SOURCE": str(source_id),
                    "PRAXIS_RECONCILE_PORT": str(fixture["port"]),
                },
            )
            report["browser_exit_code"] = await browser.wait()
            response = await client.get(f"/api/v1/runs/{crash['run_id']}")
            response.raise_for_status()
            report["final_run"] = final = response.json()
            report["after"] = await values()
            with sqlite3.connect(f"file:{workspace / 'runtime.db'}?mode=ro", uri=True) as db:
                report["events"] = [
                    {"seq": seq, "type": kind, **json.loads(payload)}
                    for seq, kind, payload in db.execute(
                        "select seq,kind,payload from agent_run_events where run_id=? order by seq",
                        (crash["run_id"],),
                    )
                ]
                report["locks_remaining"] = db.execute(
                    "select count(*) from agent_resource_locks where run_id=?", (crash["run_id"],)
                ).fetchone()[0]
                report["messages"] = [
                    json.loads(row[0])
                    for row in db.execute(
                        'select payload from agent_messages where run_id=? order by "index"',
                        (crash["run_id"],),
                    )
                ]
            assert report["browser_exit_code"] == 0
            assert final["status"] == "finished"
            assert report["before"] == report["after"] and report["locks_remaining"] == 0
            assert (
                sum(
                    event["type"] == "tool_start" and event["name"] == "request_database_change"
                    for event in report["events"]
                )
                == 1
            )
            report["recovery_passed"] = True
            print(
                json.dumps(
                    {
                        "run_id": crash["run_id"],
                        "status": final["status"],
                        "output": final["output"],
                        "values": report["after"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    except BaseException as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        if reader is not None:
            reader.close()
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 15)
            except TimeoutError:
                process.kill()
                await process.wait()
        log.close()
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2).replace(password, "[redacted]")
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--crash-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
