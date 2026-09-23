"""Offload synchronous transactions/initialization and drain owned async work."""

import asyncio
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


async def run_db(operation: Callable[..., T], *args, **kwargs) -> T:
    # Transactions own their sessions inside the worker. Never move a live
    # session between tasks or abandon a mutating transaction on cancellation.
    return await await_completion(
        asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    )


async def await_completion(task: asyncio.Task[T]) -> T:
    """Drain owned work before propagating cancellation, including repeated cancel."""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                # Observe failure even when the owner was cancelled.
                task.result()
                raise
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        raise asyncio.CancelledError
    return result
