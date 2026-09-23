"""Cold initialization must not stop leases or orphan owned connections."""

import asyncio
import threading

import httpx2
import pytest
from test_models import config
from test_provider import stream_response, use_mock_transport
from test_service import settled, submit

from app.services.agent import models
from app.services.agent.models import ModelFactory
from app.services.agent.persistence import run_db
from app.services.agent.service import AgentRunService


def hold_construction(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = models.OpenAIChatModel

    def construct(*args, **kwargs):
        entered.set()
        assert release.wait(4), "The event loop did not release cold initialization"
        return original(*args, **kwargs)

    monkeypatch.setattr(models, "OpenAIChatModel", construct)
    return entered, release


@pytest.mark.parametrize("cancel", [False, True])
async def test_cold_sdk_initialization_keeps_lease_and_obeys_cancel(store, monkeypatch, cancel):
    requests = []

    def response(request):
        requests.append(request)
        return stream_response({"content": "完成。"})

    use_mock_transport(monkeypatch, response)
    entered, release = hold_construction(monkeypatch)
    factory = ModelFactory(config)
    service = AgentRunService(
        store,
        factory.get_model,
        {},
        capabilities_for_run=lambda _: frozenset(),
        model_snapshot_factory=factory.snapshot,
        poll_seconds=0.01,
    )
    await service.start()
    try:
        run = submit(service)
        assert await asyncio.to_thread(entered.wait, 2)
        # Deliberately exceed the unchanged fixture lease, before request_started.
        await asyncio.sleep(store.lease_seconds + 0.2)
        row = await run_db(store.get, run["id"], "user")
        assert row["status"] == "running"
        assert not requests
        if cancel:
            await run_db(service.cancel, run["id"], "user")
        release.set()
        row = await settled(store, run["id"], status="cancelled" if cancel else "finished")
        assert len(requests) == (0 if cancel else 1)
        assert row["error_code"] is None
    finally:
        release.set()
        await service.close()
        clients = list(factory._clients)
        await factory.close()
        assert clients and all(client.is_closed() for client in clients)


async def test_concurrent_cold_starts_share_one_owned_model(monkeypatch):
    entered, release = hold_construction(monkeypatch)
    factory = ModelFactory(config)
    pending = [asyncio.create_task(factory.get_model()) for _ in range(64)]
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        # More callers than default executor workers must not starve a heartbeat
        # transaction while they wait for the same cold model.
        async with asyncio.timeout(0.5):
            assert await run_db(lambda: "heartbeat") == "heartbeat"
        release.set()
        results = await asyncio.gather(*pending)
        assert all(model is results[0] for model in results)
        assert len(factory._clients) == 1
    finally:
        release.set()
        await asyncio.gather(*pending, return_exceptions=True)
        clients = list(factory._clients)
        await factory.close()
        assert all(client.is_closed() for client in clients)


async def test_cancelled_creation_and_repeated_cancelled_close_drain_the_client(monkeypatch):
    entered, release = hold_construction(monkeypatch)
    factory = ModelFactory(config)
    creation = asyncio.create_task(factory.get_model())
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        creation.cancel()
        clients = list(factory._clients)
        assert len(clients) == 1
        closing = asyncio.create_task(factory.close())
        await asyncio.sleep(0.02)
        closing.cancel()
        await asyncio.sleep(0.02)
        closing.cancel()
        assert not creation.done() and not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await creation
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert clients[0].is_closed()
        assert not factory._models and not factory._clients
        with pytest.raises(RuntimeError, match="closed"):
            await factory.get_model()
        await factory.close()
    finally:
        release.set()
        await asyncio.gather(creation, *([closing] if closing else []), return_exceptions=True)
        await factory.close()


async def test_failed_model_construction_still_closes_its_transport(monkeypatch):
    def fail(*_args, **_kwargs):
        raise ValueError("invalid provider model")

    monkeypatch.setattr(models, "OpenAIChatModel", fail)
    factory = ModelFactory(config)
    with pytest.raises(ValueError, match="invalid provider"):
        await factory.get_model()
    clients = list(factory._clients)
    assert len(clients) == 1
    await factory.close()
    assert clients[0].is_closed()


async def test_one_shot_cold_start_does_not_block_other_loop_tasks(monkeypatch):
    use_mock_transport(
        monkeypatch,
        lambda _: httpx2.Response(
            200,
            json={
                "id": "one-shot",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "标题"},
                        "finish_reason": "stop",
                    }
                ],
            },
        ),
    )
    entered, release = hold_construction(monkeypatch)
    factory = ModelFactory(config)
    pending = asyncio.create_task(factory.text(instructions="标题", prompt="输入", purpose="test"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.sleep(0.02)
        assert not pending.done()
        release.set()
        assert await pending == "标题"
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await factory.close()
