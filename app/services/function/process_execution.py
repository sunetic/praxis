"""Owned, cancellable Function processes. This is lifecycle control, NOT a sandbox.

The existing production capability boundary is unchanged. Draft validation still
requires isolated_probe; unavailable isolation never falls back to this worker.
"""

import asyncio
import json
import math
import os
import signal
import sys


class FunctionProcessError(RuntimeError):
    def __init__(self, error_type: str, detail: str):
        super().__init__(detail)
        self.error_type = error_type


async def _bounded_read(pipe, limit: int) -> bytes:
    chunks = bytearray()
    while chunk := await pipe.read(65536):
        chunks.extend(chunk)
        if len(chunks) > limit:
            raise ValueError("Function process output limit exceeded")
    return bytes(chunks)


def _kill_owned_process(process) -> None:
    try:
        if os.name == "posix":
            # start_new_session assigns this invocation its own process group.
            # Include ordinary descendants, even if the leader has already exited.
            os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
    except ProcessLookupError:
        pass


async def _settle(task) -> None:
    """Repeated cancellation cannot let the caller leave a child uncollected."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


class FunctionProcesses:
    def __init__(self, max_workers: int = 1):
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self._slots = asyncio.Semaphore(max_workers)
        self._tasks: set[asyncio.Task] = set()
        self._closed = False

    async def execute(self, request: dict, *, timeout: float):
        if self._closed:
            raise RuntimeError("Function runtime is closed")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Function timeout must be finite and positive")
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            async with asyncio.timeout(timeout):
                async with self._slots:
                    return await self._run(request)
        except TimeoutError as exc:
            raise TimeoutError("Function execution timed out; local process stopped") from exc
        finally:
            self._tasks.discard(task)

    async def _run(self, request):
        encoded = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
        # Shield process creation: if cancellation races spawn, obtain its handle
        # before cleanup instead of losing a newly created child.
        launch = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "app.services.function.process_worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
        )
        process = None
        tasks = []
        try:
            process = await asyncio.shield(launch)

            async def send():
                try:
                    process.stdin.write(encoded)
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    process.stdin.close()

            tasks = [
                asyncio.create_task(_bounded_read(process.stdout, 8 * 1024 * 1024)),
                asyncio.create_task(_bounded_read(process.stderr, 64 * 1024)),
                asyncio.create_task(send()),
                asyncio.create_task(process.wait()),
            ]
            stdout, _stderr, _, code = await asyncio.gather(*tasks)
            if code != 0:
                raise RuntimeError(f"Function process exited without a result (exit {code})")
            result = json.loads(stdout)
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ValueError("Invalid Function process result")
            if not result["ok"]:
                raise FunctionProcessError(result["error_type"], result["detail"])
            return result["output"]
        finally:

            async def collect():
                child = process or await launch
                _kill_owned_process(child)
                for pending in tasks:
                    pending.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await child.wait()

            await _settle(asyncio.create_task(collect()))

    async def close(self):
        self._closed = True
        pending = list(self._tasks)
        for task in pending:
            task.cancel()
        if pending:

            async def collect():
                await asyncio.gather(*pending, return_exceptions=True)

            await _settle(asyncio.create_task(collect()))
