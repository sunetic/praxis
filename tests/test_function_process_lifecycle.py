"""Execute trusted fixtures to verify process ownership, not sandbox security."""

import asyncio
import os

import pytest

from app.services.function.process_execution import FunctionProcesses


def request(code, **payload):
    return {"code_snapshot": code, "payload": payload, "context": {}, "runtime_services": {}}


async def wait_for_file(path):
    async with asyncio.timeout(10):
        while not path.exists() or not path.read_text():
            await asyncio.sleep(0.01)


RUNNING_CODE = """
import os, time
from pathlib import Path
Path(payload['pid']).write_text(str(os.getpid()))
while True:
    Path(payload['counter']).write_text(str(time.monotonic_ns()))
    time.sleep(0.01)
"""


@pytest.mark.parametrize("stop", ["cancel", "timeout", "close", "repeated_cancel"])
async def test_stop_reaps_process_and_prevents_later_local_effects(tmp_path, stop):
    runtime = FunctionProcesses()
    pid_file, counter = tmp_path / "pid", tmp_path / "counter"
    task = asyncio.create_task(
        runtime.execute(
            request(RUNNING_CODE, pid=str(pid_file), counter=str(counter)),
            timeout=3 if stop == "timeout" else 30,
        )
    )
    try:
        await wait_for_file(counter)
        pid = int(pid_file.read_text())
        if stop == "close":
            await runtime.close()
        elif stop != "timeout":
            task.cancel()
            if stop == "repeated_cancel":
                await asyncio.sleep(0)
                task.cancel()
        with pytest.raises(TimeoutError if stop == "timeout" else asyncio.CancelledError):
            await task
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        final_counter = counter.read_text()
        await asyncio.sleep(0.1)
        assert counter.read_text() == final_counter
        assert not runtime._tasks
        if stop == "close":
            with pytest.raises(RuntimeError, match="closed"):
                await runtime.execute(request("result = 1"), timeout=10)
        else:
            assert await runtime.execute(request("result = 42"), timeout=10) == 42
    finally:
        await runtime.close()


async def test_queue_timeout_never_starts_second_process(tmp_path):
    runtime = FunctionProcesses(max_workers=1)
    first = asyncio.create_task(
        runtime.execute(
            request(
                RUNNING_CODE,
                pid=str(tmp_path / "pid"),
                counter=str(tmp_path / "counter"),
            ),
            timeout=30,
        )
    )
    try:
        await wait_for_file(tmp_path / "counter")
        never = tmp_path / "never"
        with pytest.raises(TimeoutError):
            await runtime.execute(
                request(
                    "from pathlib import Path\nPath(payload['marker']).touch()\nresult = 1",
                    marker=str(never),
                ),
                timeout=0.05,
            )
        assert not never.exists()
        assert not first.done()
    finally:
        await runtime.close()
    assert first.cancelled()


async def test_cancel_does_not_kill_another_invocation(tmp_path):
    runtime = FunctionProcesses(max_workers=2)
    tasks = [
        asyncio.create_task(
            runtime.execute(
                request(
                    RUNNING_CODE,
                    pid=str(tmp_path / f"pid-{i}"),
                    counter=str(tmp_path / f"counter-{i}"),
                ),
                timeout=30,
            )
        )
        for i in range(2)
    ]
    try:
        await asyncio.gather(*(wait_for_file(tmp_path / f"counter-{i}") for i in range(2)))
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        before = (tmp_path / "counter-1").read_text()
        await asyncio.sleep(0.1)
        assert (tmp_path / "counter-1").read_text() != before
        assert not tasks[1].done()
    finally:
        await runtime.close()
    assert tasks[1].cancelled()


@pytest.mark.skipif(os.name != "posix", reason="POSIX invocation process group")
async def test_normal_return_does_not_leave_background_descendant(tmp_path):
    runtime = FunctionProcesses()
    child_code = (
        "import time\nfrom pathlib import Path\n"
        f"p = Path({str(tmp_path / 'child-counter')!r})\n"
        "while True:\n    p.write_text(str(time.monotonic_ns()))\n    time.sleep(0.01)\n"
    )
    code = """
import subprocess, sys, time
from pathlib import Path
subprocess.Popen([sys.executable, '-c', payload['child']],
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while not Path(payload['counter']).exists():
    time.sleep(0.01)
result = 7
"""
    try:
        assert (
            await runtime.execute(
                request(
                    code,
                    child=child_code,
                    counter=str(tmp_path / "child-counter"),
                ),
                timeout=10,
            )
            == 7
        )
        counter = (tmp_path / "child-counter").read_text()
        await asyncio.sleep(0.1)
        assert (tmp_path / "child-counter").read_text() == counter
    finally:
        await runtime.close()


@pytest.mark.parametrize(
    "code, error",
    [
        ("import os\nos._exit(3)", RuntimeError),
        ("print('x' * 100000)\nresult = 1", ValueError),
    ],
)
async def test_abnormal_exit_and_excessive_output_release_capacity(code, error):
    runtime = FunctionProcesses()
    try:
        with pytest.raises(error):
            await runtime.execute(request(code), timeout=10)
        assert not runtime._tasks
        assert await runtime.execute(
            request("print('a log line')\nresult = {'ok': True}"), timeout=10
        ) == {"ok": True}
    finally:
        await runtime.close()


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
async def test_invalid_timeout_is_rejected_before_spawn(timeout):
    runtime = FunctionProcesses()
    with pytest.raises(ValueError, match="finite and positive"):
        await runtime.execute(request("result = 1"), timeout=timeout)
    assert not runtime._tasks
