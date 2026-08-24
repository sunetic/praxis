"""Hold realistic MySQL lock and transaction anomalies open during Eval."""

from __future__ import annotations

import asyncio
import contextlib
import os

import aiomysql


async def _connect(port: int, program_name: str) -> aiomysql.Connection:
    return await aiomysql.connect(
        host="127.0.0.1",
        port=port,
        user=os.environ["MYSQL_DBA_LAB_USER"],
        password=os.environ["MYSQL_DBA_LAB_PASSWORD"],
        db=os.environ["MYSQL_DBA_LAB_DATABASE"],
        autocommit=False,
        program_name=program_name,
    )


async def _execute(connection: aiomysql.Connection, sql: str) -> None:
    async with connection.cursor() as cursor:
        await cursor.execute(sql)


async def main() -> None:
    """Create the stable blocking, idle-transaction, and pool fixture."""
    port_text = os.environ.get("MYSQL_DBA_LAB_PORT", "").strip()
    if not port_text:
        raise RuntimeError("MYSQL_DBA_LAB_PORT is required")
    port = int(port_text)
    holder = await _connect(port, "config_sync_worker")
    waiter = await _connect(port, "checkout_api")
    idle_one = await _connect(port, "billing_worker")
    idle_two = await _connect(port, "billing_worker")
    sleepers = [await _connect(port, "reporting_pool") for _ in range(8)]

    await holder.begin()
    await _execute(
        holder,
        "UPDATE operations.feature_flags SET updated_at = UTC_TIMESTAMP(6) "
        "WHERE flag_key = 'checkout_v2'",
    )
    await idle_one.begin()
    await _execute(
        idle_one,
        "SELECT COUNT(*) FROM commerce.payments WHERE payment_status = 'failed'",
    )
    await idle_two.begin()
    await _execute(idle_two, "SELECT COUNT(*) FROM commerce.orders WHERE status = 'pending'")
    await _execute(waiter, "SET SESSION innodb_lock_wait_timeout = 3600")
    await waiter.begin()
    waiter_task = asyncio.create_task(
        _execute(
            waiter,
            "UPDATE operations.feature_flags SET rollout_percent = 30 "
            "WHERE flag_key = 'checkout_v2'",
        )
    )
    await asyncio.sleep(1)
    print(
        "fixture_ready holder=config_sync_worker waiter=checkout_api idle_tx=2 reporting_idle=8",
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        waiter_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiter_task
        for connection in [*sleepers, idle_two, idle_one, waiter, holder]:
            connection.close()


if __name__ == "__main__":
    asyncio.run(main())
