import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from test_function_authoring import authoring as authoring_fixture
from test_function_authoring import report, save

from app.api.functions import InputSuggestion, router
from app.models.models import Function

authoring = authoring_fixture


@pytest.mark.parametrize("runtime_path", ["draft", "production"])
async def test_input_suggestion_uses_exact_selected_source_without_execution(
    authoring, runtime_path
):
    store, function_id = authoring
    revision = save(authoring)
    validation = report(authoring, revision)
    published = store.publish(
        function_id, expected_revision=revision["revision_hash"], validation_id=validation["id"]
    )
    new_revision = save(authoring, "def main(payload, context):\n    return payload['new']\n")
    proposal = InputSuggestion(
        payload={}, rationale="需要确认业务输入", missing_information=["业务输入"], assumptions=[]
    )

    async def suggest(**kwargs):
        # No request-held Session/transaction blocks independent writes while the model waits.
        with store.sessions.begin() as db:
            db.get(Function, function_id).description = "updated while waiting"
        source = json.loads(kwargs["prompt"])
        assert kwargs["purpose"] == "function_input_suggestion"
        if runtime_path == "production":
            assert source["code"] == "def main(payload, context):\n    return payload\n"
            assert source["source"] == {"release_id": published["release_id"]}
        else:
            assert "payload['new']" in source["code"]
            assert source["source"] == {"revision_hash": new_revision["revision_hash"]}
        return proposal

    models = SimpleNamespace(structured=AsyncMock(side_effect=suggest))
    app = FastAPI()
    app.state.agent_runtime = SimpleNamespace(sessions=store.sessions, models=models)
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/functions/{function_id}/suggest-input",
            json={"runtime_path": runtime_path, "prompt": "请建议入参"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["payload"] == {}
    assert response.json()["runtime_path"] == runtime_path
    models.structured.assert_awaited_once()
    assert store.read(function_id)["revision_id"] == new_revision["revision_id"]
    assert store.read(function_id)["released_revision_id"] == revision["revision_id"]


@pytest.mark.parametrize(
    "error, status", [(ValueError("bad output"), 422), (RuntimeError("provider failed"), 502)]
)
async def test_input_proposal_failure_is_visible_without_repair(authoring, error, status):
    store, function_id = authoring
    save(authoring)
    models = SimpleNamespace(structured=AsyncMock(side_effect=error))
    app = FastAPI()
    app.state.agent_runtime = SimpleNamespace(sessions=store.sessions, models=models)
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing_release = await client.post(f"/functions/{function_id}/suggest-input", json={})
        assert missing_release.status_code == 409
        models.structured.assert_not_awaited()
        response = await client.post(
            f"/functions/{function_id}/suggest-input", json={"runtime_path": "draft"}
        )
    assert response.status_code == status
    models.structured.assert_awaited_once()
    assert store.read(function_id)["current_release_id"] is None
