"""Controlled runtime probe in an OS namespace, never in the API process.

Missing or denied isolation is an unavailable check, not permission to execute
generated Python on the host. No credentials or production database are mounted.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from app.services.function.native_authoring import static_checks


async def check_draft(code: str, payload: dict) -> list[dict]:
    checks = static_checks(code)
    if all(check["status"] == "passed" for check in checks):
        checks.append(await controlled_probe(code, payload))
    else:
        checks.append(
            {
                "name": "controlled_runtime",
                "status": "not_run",
                "executed": False,
                "diagnostic": "Static checks failed",
            }
        )
    return checks


async def controlled_probe(code: str, payload: dict, *, timeout: float = 15) -> dict:
    bwrap = shutil.which("bwrap") if sys.platform == "linux" else None
    base = {
        "name": "controlled_runtime",
        "executed": False,
        "environment": "isolated process with simulated database/platform capabilities; not a live integration test",
    }
    if not bwrap:
        return {
            **base,
            "status": "unavailable",
            "diagnostic": "Linux bubblewrap isolation is not installed",
        }
    with tempfile.TemporaryDirectory(prefix="praxis-function-probe-") as directory:
        workspace = Path(directory)
        (workspace / "main.py").write_text(code, encoding="utf-8")
        project = Path(__file__).resolve().parents[3]
        # Mount only interpreter/dependencies and application modules, not .env,
        # the workspace root, user home, or project databases.
        command = [
            bwrap,
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--clearenv",
            "--ro-bind",
            "/usr",
            "/usr",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
        ]
        for name in ("lib", "lib64", "bin"):
            path = Path("/") / name
            if path.is_symlink():
                command += ["--symlink", os.readlink(path), str(path)]
            elif path.exists():
                command += ["--ro-bind", str(path), str(path)]
        for path in {Path(sys.base_prefix), Path(sys.prefix)}:
            if path != Path("/usr"):
                command += ["--ro-bind", str(path), str(path)]
        command += [
            "--ro-bind",
            str(project / "app"),
            "/app/app",
            "--ro-bind",
            str(workspace),
            "/work",
            "--chdir",
            "/work",
            "--setenv",
            "PYTHONPATH",
            "/app",
            "--setenv",
            "PYTHONDONTWRITEBYTECODE",
            "1",
            "--setenv",
            "DATABASE_URL",
            "sqlite:////tmp/control.db",
            "--setenv",
            "DATA_DIR",
            "/tmp/data",
            "--setenv",
            "TRACING_ENABLED",
            "false",
            "--",
            sys.executable,
            "-m",
            "app.services.function.probe_worker",
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            return {
                **base,
                "status": "unavailable",
                "diagnostic": "Isolated probe launcher is not executable",
            }
        started = False

        async def read_bounded(pipe, *, observe_start=False):
            nonlocal started
            collected = bytearray()
            while chunk := await pipe.read(4096):
                collected.extend(chunk)
                if observe_start and b"\n" in collected:
                    try:
                        started = json.loads(collected.split(b"\n", 1)[0]) == {"event": "ready"}
                    except ValueError:
                        pass
                if len(collected) > 64 * 1024:
                    raise ValueError("Probe output limit exceeded")
            return bytes(collected)

        async def send():
            try:
                process.stdin.write(json.dumps(payload).encode())
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        tasks = [
            asyncio.create_task(read_bounded(process.stdout, observe_start=True)),
            asyncio.create_task(read_bounded(process.stderr)),
            asyncio.create_task(send()),
            asyncio.create_task(process.wait()),
        ]
        try:
            async with asyncio.timeout(timeout):
                stdout, _stderr, _, _ = await asyncio.gather(*tasks)
        except (TimeoutError, ValueError, asyncio.CancelledError) as exc:
            if process.returncode is None:
                process.kill()
            await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return {
                **base,
                "status": "failed" if started else "unavailable",
                "executed": started,
                "diagnostic": "Controlled probe exceeded its time or output limit",
            }
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if process.returncode != 0:
            # Do not expose host paths or environment through launcher diagnostics.
            return {
                **base,
                "status": "failed" if started else "unavailable",
                "executed": started,
                "diagnostic": "Isolated probe exited without a result"
                if started
                else "Isolated probe could not start",
                "exit_code": process.returncode,
            }
        try:
            lines = stdout.splitlines()
            report = json.loads(lines[1]) if len(lines) == 2 else None
            if (
                not started
                or not isinstance(report, dict)
                or report.get("event") != "result"
                or type(report.get("passed")) is not bool
            ):
                raise ValueError("Invalid probe report")
        except (ValueError, UnicodeDecodeError):
            return {
                **base,
                "status": "failed",
                "executed": started,
                "diagnostic": "Controlled probe produced an invalid report",
            }
        return {
            **base,
            "status": "passed" if report["passed"] else "failed",
            "executed": True,
            "diagnostic": report.get("error"),
            "result_type": report.get("result_type"),
            "payload": payload,
        }
