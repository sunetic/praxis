"""Deterministic Page checks; no model calls, retries, or completion decisions."""

import asyncio
import json
import os
import shutil
import signal
from pathlib import Path

from app.services.agent.persistence import run_db
from app.services.page.contracts import PageSource

ROOT = Path(__file__).resolve().parents[3]
WORKER = Path(__file__).with_name("check_worker.mjs")
MODULES = ROOT / "frontend" / "node_modules"


class CheckProcessError(ValueError):
    def __init__(self, *, started):
        super().__init__("Page check exceeded execution limits or returned an invalid report")
        self.started = started


async def _process(command, payload, *, timeout=30, limit=8_000_000):
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env={"PATH": os.defpath, "NODE_ENV": "production"},
    )

    started = False

    async def read(pipe, observe=False):
        nonlocal started
        chunks = bytearray()
        while chunk := await pipe.read(8192):
            chunks.extend(chunk)
            if observe and b"\n" in chunks:
                try:
                    started = json.loads(chunks.split(b"\n", 1)[0]) == {"event": "ready"}
                except ValueError:
                    pass
            if len(chunks) > limit:
                raise ValueError("Page check exceeded its output limit")
        return bytes(chunks)

    async def send():
        try:
            process.stdin.write(json.dumps(payload).encode())
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    tasks = [
        asyncio.create_task(read(process.stdout, observe=True)),
        asyncio.create_task(read(process.stderr)),
        asyncio.create_task(send()),
        asyncio.create_task(process.wait()),
    ]
    try:
        async with asyncio.timeout(timeout):
            stdout, _, _, _ = await asyncio.gather(*tasks)
        return [json.loads(line) for line in stdout.splitlines()], process.returncode
    except (ValueError, TimeoutError) as exc:
        raise CheckProcessError(started=started) from exc
    finally:
        # esbuild and the browser can have children even after Node exits.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def compile_source(source: PageSource):
    base = {"name": "source_compile", "executed": False}
    node = shutil.which("node")
    if not node or not (MODULES / "esbuild").is_dir():
        return {
            **base,
            "status": "unavailable",
            "diagnostic": "Page compiler dependencies are not installed",
        }, None
    try:
        reports, code = await _process(
            [node, "--max-old-space-size=256", str(WORKER), "compile", str(MODULES)],
            source.model_dump(mode="json"),
        )
        if code != 0 or len(reports) != 2 or reports[0] != {"event": "ready"}:
            started = bool(reports and reports[0] == {"event": "ready"})
            return {
                "name": "source_compile",
                "executed": started,
                "status": "failed" if started else "unavailable",
                "diagnostic": "Page compiler stopped without a valid report",
            }, None
        report = reports[-1]
        if report.get("status") not in {"passed", "failed"} or report.get("executed") is not True:
            raise CheckProcessError(started=True)
        html = report.pop("html", None)
        if report.get("status") == "passed" and not isinstance(html, str):
            raise CheckProcessError(started=True)
        return {"name": "source_compile", **report}, html
    except CheckProcessError as exc:
        return {
            "name": "source_compile",
            "executed": exc.started,
            "status": "failed" if exc.started else "unavailable",
            "diagnostic": str(exc),
        }, None
    except (OSError, ValueError, TimeoutError):
        return {
            **base,
            "status": "unavailable",
            "diagnostic": "Page compiler exceeded limits or could not return a report",
        }, None


async def browser_check(html):
    base = {"name": "browser_runtime", "executed": False}
    node, bwrap = shutil.which("node"), shutil.which("bwrap")
    if not node or not bwrap or not (MODULES / "@playwright/test").is_dir():
        return {
            **base,
            "status": "unavailable",
            "diagnostic": "Isolated browser dependencies are not installed",
        }
    try:
        locations, code = await _process([node, str(WORKER), "browser-path", str(MODULES)], {})
        executable = Path(locations[0]["path"]) if code == 0 else None
        if not executable or not executable.is_file():
            raise ValueError("Browser executable is unavailable")
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
        command += [
            "--ro-bind",
            str(Path(node).resolve()),
            "/node",
            "--ro-bind",
            str(executable.parent),
            "/browser",
            "--ro-bind",
            str(MODULES),
            "/packages",
            "--ro-bind",
            str(WORKER),
            "/worker.mjs",
            "--setenv",
            "HOME",
            "/tmp",
            "--",
            "/node",
            "--max-old-space-size=256",
            "/worker.mjs",
            "browser",
            "/packages",
        ]
        reports, code = await _process(command, {"html": html}, timeout=20, limit=100_000)
        started = bool(reports and reports[0] == {"event": "ready"})
        if code != 0 or not reports:
            return {
                "name": "browser_runtime",
                "executed": started,
                "status": "failed" if started else "unavailable",
                "diagnostic": "Isolated browser stopped without a valid report"
                if started
                else "Isolated browser launcher could not start",
                "exit_code": code,
            }
        report = reports[-1]
        if (
            report.get("status") not in {"passed", "failed", "unavailable"}
            or type(report.get("executed")) is not bool
            or report["executed"] != started
            or (report["status"] == "unavailable") == started
            or len(reports) != (2 if started else 1)
        ):
            raise CheckProcessError(started=started)
        return {"name": "browser_runtime", **report}
    except CheckProcessError as exc:
        return {
            "name": "browser_runtime",
            "executed": exc.started,
            "status": "failed" if exc.started else "unavailable",
            "diagnostic": str(exc),
        }
    except (OSError, ValueError, KeyError, IndexError, TimeoutError):
        return {
            **base,
            "status": "unavailable",
            "diagnostic": "Isolated browser exceeded limits or could not return a report",
        }


async def validate_page(store, page_id, *, expected_revision, run_id):
    from app.services.function.native_authoring import AuthoringError

    current = await run_db(store.read, page_id)
    if current["revision_hash"] != expected_revision or not current["revision_id"]:
        raise AuthoringError("Save and read the exact Page revision before checking")
    source = PageSource(files=current["files"], bindings=current["bindings"])
    compiled, html = await compile_source(source)
    bindings = await run_db(store.inspect_bindings, current["bindings"])
    browser = (
        await browser_check(html)
        if html is not None
        else {
            "name": "browser_runtime",
            "status": "not_run",
            "executed": False,
            "diagnostic": "Source compilation did not produce an artifact",
        }
    )
    # A DOM render and valid release metadata do not prove actual API interaction.
    integration = {
        "name": "binding_runtime",
        "status": "not_run",
        "executed": False,
        "applicable": bool(current["bindings"]),
        "diagnostic": "Live Function binding interactions have not been executed"
        if current["bindings"]
        else "Not applicable: no Function bindings are declared",
    }
    return await run_db(
        store.record_validation,
        page_id,
        revision_id=current["revision_id"],
        revision_hash=expected_revision,
        checks=[compiled, bindings, browser, integration],
        html=html,
        run_id=run_id,
    )
