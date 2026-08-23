"""Hold realistic lock and session anomalies open during the PG DBA eval."""

from __future__ import annotations

import asyncio
import os

import asyncpg


async def _connect(dsn: str, app_name: str) -> asyncpg.Connection:
    return await asyncpg.connect(dsn, server_settings={"application_name": app_name})


async def main() -> None:
    """Create the stable blocking, idle-transaction and pool fixture."""
    dsn = os.environ.get("PG_DBA_LAB_DSN", "").strip()
    if not dsn:
        raise RuntimeError("PG_DBA_LAB_DSN is required")
    holder = await _connect(dsn, "praxis_lock_holder")
    waiter = await _connect(dsn, "checkout_api_waiter")
    idle_one = await _connect(dsn, "legacy_billing_idle_tx")
    idle_two = await _connect(dsn, "legacy_billing_idle_tx")
    sleepers = [await _connect(dsn, "reporting_pool") for _ in range(8)]

    await holder.execute("BEGIN")
    await holder.execute(
        "UPDATE operations.feature_flags SET updated_at = now() WHERE flag_key = 'checkout_v2'"
    )
    await idle_one.execute("BEGIN")
    await idle_one.fetchval(
        "SELECT count(*) FROM commerce.payments WHERE payment_status = 'failed'"
    )
    await idle_two.execute("BEGIN")
    await idle_two.fetchval("SELECT count(*) FROM commerce.orders WHERE status = 'pending'")
    waiter_task = asyncio.create_task(
        waiter.execute(
            "UPDATE operations.feature_flags SET rollout_percent = 30 WHERE flag_key = 'checkout_v2'"
        )
    )
    print(
        "fixture_ready holder=praxis_lock_holder waiter=checkout_api_waiter "
        "idle_tx=2 reporting_idle=8",
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        waiter_task.cancel()
        for connection in [*sleepers, idle_two, idle_one, waiter, holder]:
            await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
