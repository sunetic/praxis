import asyncio
import threading
from time import monotonic
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.agent_runs import RUNTIME_TABLES
from app.services.agent.definitions import AgentDefinition
from app.services.agent.persistence import run_db
from app.services.agent.service import AgentRunService
from app.services.agent.store import RunStore


async def test_dispatcher_waiting_for_a_db_connection_does_not_block_the_loop(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'small-pool.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.5,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=RUNTIME_TABLES)
    service = AgentRunService(
        RunStore(sessionmaker(engine)),
        AsyncMock(return_value=None),
        {},
        capabilities_for_run=lambda _: frozenset(),
        poll_seconds=0.01,
    )
    await service.start()
    try:
        # The HTTP dependency's teardown needs the loop to run before it can
        # release its connection. Reproduce that dependency with one connection.
        with engine.connect():
            started = monotonic()
            await asyncio.sleep(0.04)
            assert monotonic() - started < 0.3
    finally:
        await service.close()
        engine.dispose()


async def test_cancellation_drains_the_transaction_before_returning(store):
    started, release = threading.Event(), threading.Event()

    def transaction():
        started.set()
        assert release.wait(2)
        store.create_conversation("committed-on-cancel", "user")

    pending = asyncio.create_task(run_db(transaction))
    assert await asyncio.to_thread(started.wait, 2)
    pending.cancel()
    await asyncio.sleep(0.01)
    assert not pending.done()
    pending.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert store.get_conversation("committed-on-cancel", "user")["id"] == "committed-on-cancel"


async def test_db_errors_propagate_without_hidden_retry():
    calls = []

    def transaction():
        calls.append(1)
        raise ValueError("transaction failed")

    with pytest.raises(ValueError, match="transaction failed"):
        await run_db(transaction)
    assert calls == [1]


async def test_model_snapshot_does_not_need_two_simultaneous_connections(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'snapshot-pool.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.2,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=RUNTIME_TABLES)
    store = RunStore(sessionmaker(engine))
    store.create_conversation("small-pool", "user")

    def snapshot():
        # Mirrors the platform model-settings reader's independent session.
        with engine.connect():
            return {"test_snapshot": True}

    try:
        run = await run_db(
            store.submit,
            "small-pool",
            "user",
            "request",
            "hello",
            AgentDefinition(name="no-tools", tool_names=frozenset()),
            model_snapshot_factory=snapshot,
        )
        assert run["model_snapshot"] == {"test_snapshot": True}
    finally:
        engine.dispose()
