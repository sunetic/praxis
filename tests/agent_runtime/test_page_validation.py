"""Check lifecycle contracts; mocked browser reports are not live E2E evidence."""

import asyncio
import signal

import pytest
from test_function_isolation import Process

from app.services.page import validation
from app.services.page.contracts import PageSource


@pytest.mark.parametrize("started", [False, True])
async def test_compiler_crash_preserves_whether_it_really_started(monkeypatch, started):
    async def process(*_args, **_kwargs):
        return ([{"event": "ready"}] if started else []), 1

    monkeypatch.setattr(validation, "_process", process)
    check, html = await validation.compile_source(PageSource(files={}))
    assert check["status"] == ("failed" if started else "unavailable")
    assert check["executed"] is started
    assert html is None


@pytest.mark.parametrize("mode", ["timeout", "output", "cancel"])
async def test_page_check_limits_and_cancel_kill_owned_group_and_reap(monkeypatch, mode):
    process = Process(
        b'{"event":"ready"}\n' + (b"x" * 2048 if mode == "output" else b""), hang=True
    )
    process.pid = 12345678
    entered = asyncio.Event()

    async def spawn(*_args, **kwargs):
        assert kwargs["start_new_session"] is True
        assert set(kwargs["env"]) == {"PATH", "NODE_ENV"}
        entered.set()
        return process

    def killpg(pid, sig):
        assert pid == process.pid and sig == signal.SIGKILL
        process.kill()

    monkeypatch.setattr(validation.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(validation.os, "killpg", killpg)
    task = asyncio.create_task(validation._process(["trusted-check"], {}, timeout=0.05, limit=1024))
    await entered.wait()
    if mode == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(validation.CheckProcessError) as error:
            await task
        assert error.value.started is True
    assert process.killed and process.done.is_set()


async def test_missing_browser_isolation_never_falls_back_to_host_execution(monkeypatch):
    monkeypatch.setattr(validation.shutil, "which", lambda _: None)

    async def never(*_args, **_kwargs):
        pytest.fail("No subprocess may start when isolation is unavailable")

    monkeypatch.setattr(validation, "_process", never)
    check = await validation.browser_check("<script>untrusted()</script>")
    assert check["status"] == "unavailable" and check["executed"] is False


@pytest.mark.parametrize("started,code", [(False, 1), (True, 1), (True, 0)])
async def test_browser_handshake_and_exit_are_not_confused_with_unavailability(
    tmp_path, monkeypatch, started, code
):
    browser = tmp_path / "chrome"
    browser.touch()
    (tmp_path / "@playwright/test").mkdir(parents=True)
    monkeypatch.setattr(validation, "MODULES", tmp_path)
    monkeypatch.setattr(validation.shutil, "which", lambda name: f"/usr/bin/{name}")
    commands = []

    async def process(command, _payload, **_kwargs):
        commands.append(command)
        if "browser-path" in command:
            return [{"path": str(browser)}], 0
        reports = [{"event": "ready"}] if started else []
        if code == 0:
            reports.append({"executed": True, "status": "passed"})
        return reports, code

    monkeypatch.setattr(validation, "_process", process)
    check = await validation.browser_check("<main>Test</main>")
    assert check["executed"] is started
    assert check["status"] == ("passed" if code == 0 else "failed" if started else "unavailable")
    command = commands[1]
    assert "--unshare-all" in command and "--clearenv" in command
    mounts = [command[index + 1] for index, value in enumerate(command) if value == "--ro-bind"]
    assert "/" not in mounts
    assert not any(path.endswith((".env", ".db")) for path in mounts)
