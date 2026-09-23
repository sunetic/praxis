import json

from pydantic_ai import Tool
from sqlalchemy import text
from test_models import config
from test_provider import stream_response, use_mock_transport
from test_runtime import Script, call
from test_service import approve_write, settled, submit

from app.api.agent_runs import _public_run
from app.services.agent.execution import RegisteredTool
from app.services.agent.models import ModelFactory, ModelSnapshot
from app.services.agent.service import AgentRunService


async def test_approval_resume_keeps_original_model_profile_and_credential_across_restart(store):
    current = [config(model_name="before-approval")]
    factory = ModelFactory(lambda: current[0])
    writes = []
    observed = []
    script = Script([call("write")], ["已处理。"])

    async def write() -> str:
        writes.append(1)
        return "written"

    async def load_frozen_model(row):
        observed.append(ModelSnapshot.model_validate(row["model_snapshot"]).restore())
        return script.model

    def service():
        return AgentRunService(
            store,
            load_frozen_model,
            {
                "write": RegisteredTool(
                    tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
                )
            },
            capabilities_for_run=lambda _: frozenset({"write"}),
            model_snapshot_factory=factory.snapshot,
            poll_seconds=0.01,
        )

    first = service()
    await first.start()
    try:
        run = submit(first)
        waiting = await settled(store, run["id"], status="waiting_approval")
    finally:
        await first.close()
    current[0] = config(model_name="after-approval", api_key="rotated-secret")
    second = service()
    decision = waiting["approvals"][0]
    second.approve(run["id"], "user", decision["call_id"], decision["fingerprint"], True)
    await second.start()
    try:
        final = await settled(store, run["id"], status="finished")
        assert writes == [1]
        assert [item.model_name for item in observed] == ["before-approval", "before-approval"]
        assert all(item.api_key.get_secret_value() == "do-not-log-this" for item in observed)
        assert _public_run(final)["model"]["model_name"] == "before-approval"
        public = json.dumps(_public_run(final))
        assert "credential" not in public
        assert "do-not-log-this" not in public
        with store.sessions() as db:
            raw = db.execute(
                text("SELECT model_snapshot FROM agent_runs WHERE id=:id"), {"id": run["id"]}
            ).scalar_one()
        assert "do-not-log-this" not in raw
        assert "gAAAAA" in raw
    finally:
        await second.close()
        await factory.close()


async def test_duplicate_submission_does_not_reread_or_change_its_model_snapshot(store):
    factory = ModelFactory(lambda: config())
    script = Script(["完成。"])
    service = AgentRunService(
        store,
        script.factory,
        {},
        capabilities_for_run=lambda _: frozenset(),
        model_snapshot_factory=factory.snapshot,
    )
    try:
        first = submit(service)

        def unavailable():
            raise AssertionError("An idempotent retry tried to snapshot new platform settings")

        service.model_snapshot_factory = unavailable
        duplicate = submit(service)
        assert duplicate["id"] == first["id"]
        assert duplicate["model_snapshot"] == first["model_snapshot"]
    finally:
        await service.close()
        await factory.close()


async def test_real_sdk_request_after_approval_restart_uses_the_original_endpoint_and_key(
    store, monkeypatch
):
    requests = []
    writes = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert str(request.url) == "https://example.invalid/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer do-not-log-this"
        assert payload["model"] == "original-model"
        if len(requests) == 1:
            return stream_response(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "frozen-call",
                            "type": "function",
                            "function": {"name": "write", "arguments": "{}"},
                        }
                    ]
                },
                finish_reason="tool_calls",
            )
        assert any(message.get("tool_call_id") == "frozen-call" for message in payload["messages"])
        return stream_response({"content": "已处理。"}, response_id="resumed-response")

    use_mock_transport(monkeypatch, handler)

    async def write() -> str:
        writes.append(1)
        return "written"

    def service(factory):
        return AgentRunService(
            store,
            factory.get_model,
            {
                "write": RegisteredTool(
                    tool=Tool(write, sequential=True), authorize=approve_write, mutating=True
                )
            },
            capabilities_for_run=lambda _: frozenset({"write"}),
            model_snapshot_factory=factory.snapshot,
            poll_seconds=0.01,
        )

    original = ModelFactory(lambda: config(model_name="original-model"))
    first = service(original)
    await first.start()
    try:
        run = submit(first)
        waiting = await settled(store, run["id"], status="waiting_approval")
    finally:
        await first.close()
        await original.close()

    changed = ModelFactory(
        lambda: config(
            model_name="other-model", base_url="https://changed.invalid/v1", api_key="rotated-key"
        )
    )
    second = service(changed)
    decision = waiting["approvals"][0]
    second.approve(run["id"], "user", decision["call_id"], decision["fingerprint"], True)
    await second.start()
    try:
        assert (await settled(store, run["id"], status="finished"))["output"] == "已处理。"
        assert len(requests) == 2
        assert writes == [1]
    finally:
        await second.close()
        await changed.close()
