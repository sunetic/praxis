"""The settings API has one model path, not an execution-engine fallback."""

import pytest
from test_api_e2e_p0 import api_client  # noqa: F401


@pytest.mark.parametrize(
    "payload",
    [
        {"ai_action_confirmation_bypass": True},
        {"build_engine": "reasoning"},
        {"external_cli_command": "cli"},
        {"external_cli_pre_flags": "--help"},
        {"external_cli_post_flags": "--unsafe"},
    ],
)
def test_retired_engine_settings_are_not_accepted(api_client, payload):  # noqa: F811
    client, _ = api_client
    assert client.patch("/api/v1/settings", json=payload).status_code == 422
    assert client.post("/api/v1/settings/test-engine", json={"command": "cli"}).status_code == 404
    response = client.get("/api/v1/settings").json()
    assert (
        not {
            "ai_action_confirmation_bypass",
            "build_engine",
            "external_cli_command",
            "external_cli_pre_flags",
            "external_cli_post_flags",
        }
        & response.keys()
    )


def test_native_context_configuration_and_validation(api_client):  # noqa: F811
    client, _ = api_client
    response = client.patch(
        "/api/v1/settings",
        json={
            "context_window_tokens": 131072,
            "context_compression_threshold_percent": 82,
        },
    )
    assert response.status_code == 200
    assert response.json()["context_window_tokens"] == 131072
    assert response.json()["context_compression_threshold_percent"] == 82
    for threshold in (49, 96):
        assert (
            client.patch(
                "/api/v1/settings", json={"context_compression_threshold_percent": threshold}
            ).status_code
            == 422
        )
