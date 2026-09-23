import pytest
from pydantic import SecretStr, ValidationError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from app.services.agent.models import (
    ModelConnectionConfig,
    ModelFactory,
    ModelSnapshot,
    open_model,
    request_text,
)


def config(**overrides):
    return ModelConnectionConfig(
        **{
            "model_name": "test-model",
            "base_url": "https://example.invalid/v1",
            "api_key": SecretStr("do-not-log-this"),
            **overrides,
        }
    )


def test_config_does_not_serialize_or_display_credentials():
    value = config()
    assert "do-not-log-this" not in repr(value)
    assert "api_key" not in value.model_dump()
    assert "do-not-log-this" not in value.model_dump_json()


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_name": " "},
        {"base_url": "file:///tmp/model"},
        {"base_url": "https://user:password@example.invalid/v1"},
        {"base_url": "https://example.invalid/v1?token=secret"},
        {"api_key": ""},
        {"timeout_seconds": float("inf")},
        {"transport_retries": -1},
        {"unknown_setting": True},
        {"context_window_tokens": 1024, "max_output_tokens": 1024},
        {"context_compression_threshold_percent": 99},
        {"temperature": 3},
        {"top_p": 0},
    ],
)
def test_invalid_model_config_is_rejected(overrides):
    with pytest.raises(ValidationError):
        config(**overrides)


def test_snapshot_pins_capacity_output_and_sampling_settings():
    current = [
        config(context_window_tokens=8192, max_output_tokens=512, temperature=0.3, top_p=0.9)
    ]
    factory = ModelFactory(lambda: current[0])
    snapshot = ModelSnapshot.model_validate(factory.snapshot())
    current[0] = config()
    assert snapshot.configuration.context_window_tokens == 8192
    assert snapshot.configuration.trigger_tokens == 6144
    assert snapshot.restore().request_settings() == {
        "max_tokens": 512,
        "temperature": 0.3,
        "top_p": 0.9,
    }


async def test_model_connection_has_explicit_provider_and_closes_client(monkeypatch):
    for name in (
        "ALL_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "http_proxy",
        "https_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    async with open_model(config()) as model:
        assert model.model_name == "test-model"
        client = model.provider.client
        assert str(client.base_url) == "https://example.invalid/v1/"
        assert client.max_retries == 2
        assert client.timeout == 120
        assert not client.is_closed()
    assert client.is_closed()


async def test_one_shot_text_request_uses_native_messages_without_tools():
    calls = []

    def answer(messages, info):
        calls.append(messages)
        assert info.function_tools == []
        assert info.output_tools == []
        assert info.model_settings["temperature"] == 0.2
        return ModelResponse([TextPart("标题")], finish_reason="stop")

    text = await request_text(
        FunctionModel(answer),
        instructions="生成简短标题。",
        prompt="查询慢 SQL",
        model_settings={"temperature": 0.2},
    )
    assert text == "标题"
    assert len(calls) == 1
    assert [part.part_kind for part in calls[0][0].parts] == ["system-prompt", "user-prompt"]


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "error"])
async def test_partial_or_filtered_one_shot_answer_is_not_silently_accepted(finish_reason):
    def answer(_messages, _info):
        return ModelResponse([TextPart("partial")], finish_reason=finish_reason)

    with pytest.raises(ValueError, match="did not complete"):
        await request_text(FunctionModel(answer), instructions="title", prompt="input")


async def test_empty_one_shot_answer_is_not_replaced_by_a_fabricated_fallback():
    def answer(_messages, _info):
        return ModelResponse([TextPart("")], finish_reason="stop")

    with pytest.raises(ValueError, match="no text"):
        await request_text(FunctionModel(answer), instructions="title", prompt="input")


async def test_model_snapshot_survives_config_rotation_and_new_factory_without_plaintext_key():
    import json

    current = [config()]
    factory = ModelFactory(lambda: current[0])
    saved = factory.snapshot()
    assert "do-not-log-this" not in json.dumps(saved)
    snapshot = ModelSnapshot.model_validate(saved)
    assert "credential" not in snapshot.model_dump()
    assert "gAAAAA" not in repr(snapshot)
    current[0] = config(model_name="new-model", api_key="new-secret")
    first = await factory.get_model({"model_snapshot": saved})
    assert first.model_name == "test-model"
    assert first.provider.client.api_key == "do-not-log-this"
    assert (await factory.get_model()).model_name == "new-model"
    await factory.close()

    # A new application instance must not consult the now-current config.
    def forbidden_loader():
        raise AssertionError("An existing run re-read current platform settings")

    restarted = ModelFactory(forbidden_loader)
    resumed = await restarted.get_model({"model_snapshot": saved})
    assert resumed.model_name == "test-model"
    assert resumed.provider.client.api_key == "do-not-log-this"
    await restarted.close()


async def test_corrupt_or_missing_model_snapshot_does_not_fall_back_to_current_config():
    factory = ModelFactory(lambda: config())
    with pytest.raises((KeyError, ValidationError)):
        await factory.get_model({})
    saved = factory.snapshot()
    saved["credential"] = "not-an-encrypted-secret"
    with pytest.raises(ValueError, match="decrypt"):
        await factory.get_model({"model_snapshot": saved})
    assert factory._models == {}
    await factory.close()
