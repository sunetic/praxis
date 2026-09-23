import asyncio
import json

import pytest

from app.services.function import isolated_probe


class Input:
    def write(self, data):
        self.data = data

    async def drain(self):
        pass

    def close(self):
        pass


class Process:
    def __init__(self, stdout=b"", stderr=b"", code=0, hang=False):
        self.stdin = Input()
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stderr.feed_data(stderr)
        self.returncode = None if hang else code
        self.done = asyncio.Event()
        self.killed = False
        if not hang:
            self.finish()

    def finish(self):
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.done.set()

    async def wait(self):
        await self.done.wait()
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.finish()


@pytest.mark.parametrize(
    "ready,code,expected", [(False, 1, "unavailable"), (True, 1, "failed"), (True, 0, "passed")]
)
async def test_probe_reports_start_failure_separately_from_executed_failure(
    monkeypatch, ready, code, expected
):
    body = b'{"event":"ready"}\n' if ready else b""
    if code == 0:
        body += b'{"event":"result","passed":true,"result_type":"dict"}\n'
    process = Process(body, code=code)
    commands = []

    async def spawn(*command, **kwargs):
        commands.append(command)
        return process

    monkeypatch.setattr(isolated_probe.shutil, "which", lambda _: "/usr/bin/bwrap")
    monkeypatch.setattr(isolated_probe.asyncio, "create_subprocess_exec", spawn)
    result = await isolated_probe.controlled_probe(
        "def main(payload, context): return {}", {"input": 1}
    )
    assert result["status"] == expected
    assert result["executed"] == ready
    assert json.loads(process.stdin.data) == {"input": 1}
    command = commands[0]
    assert "--unshare-all" in command and "--clearenv" in command
    mounts = [command[index + 1] for index, arg in enumerate(command) if arg == "--ro-bind"]
    assert "/" not in mounts
    assert not any(path.endswith(".env") or path.endswith("runtime.db") for path in mounts)


@pytest.mark.parametrize("mode", ["timeout", "output", "cancel"])
async def test_probe_limit_and_cancellation_kill_and_reap_process(monkeypatch, mode):
    body = b'{"event":"ready"}\n' + (b"x" * 70_000 if mode == "output" else b"")
    process = Process(body, hang=True)
    entered = asyncio.Event()

    async def spawn(*_args, **_kwargs):
        entered.set()
        return process

    monkeypatch.setattr(isolated_probe.shutil, "which", lambda _: "/usr/bin/bwrap")
    monkeypatch.setattr(isolated_probe.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(isolated_probe.controlled_probe("unused", {}, timeout=0.05))
    await entered.wait()
    if mode == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await task
        assert result["status"] == "failed" and result["executed"] is True
    assert process.killed and process.done.is_set()
