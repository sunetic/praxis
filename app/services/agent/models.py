"""Native model construction and one-shot requests, without legacy response dicts."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from time import monotonic
from typing import Literal, TypeVar

from openai import AsyncOpenAI
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_ai.direct import model_request
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

from app.core.logging import get_logger
from app.services.agent.context_budget import ContextPolicy
from app.services.agent.persistence import await_completion, run_db

logger = get_logger("agent.model")
OutputT = TypeVar("OutputT", bound=BaseModel)


class ModelProfile(ContextPolicy):
    """Public, immutable provider configuration, safe for run metadata."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True
    )

    model_name: str = Field(min_length=1)
    base_url: AnyHttpUrl
    timeout_seconds: float = Field(default=120, gt=0, allow_inf_nan=False)
    transport_retries: int = Field(default=2, ge=0, le=5)
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    top_p: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)

    def request_settings(self) -> ModelSettings:
        settings: ModelSettings = {"max_tokens": self.max_output_tokens}
        if self.temperature is not None:
            settings["temperature"] = self.temperature
        if self.top_p is not None:
            settings["top_p"] = self.top_p
        return settings

    @field_validator("base_url")
    @classmethod
    def credential_free_base_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("Model base URL must not contain credentials, query or fragment")
        return value


class ModelConnectionConfig(ModelProfile):
    """Resolved connection; credentials are excluded from repr and serialization."""

    api_key: SecretStr = Field(exclude=True, repr=False, min_length=1)


class ModelSnapshot(BaseModel):
    """Private persisted configuration. Only encrypted credentials cross the DB boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    schema_version: Literal[1] = 1
    configuration: ModelProfile
    credential: SecretStr = Field(exclude=True, repr=False)

    def restore(self) -> ModelConnectionConfig:
        from app.core.security import decrypt_secret

        return ModelConnectionConfig(
            **self.configuration.model_dump(),
            api_key=decrypt_secret(self.credential.get_secret_value()),
        )


def resolve_model_config() -> ModelConnectionConfig:
    """Resolve current platform settings, failing visibly on DB/decryption errors."""
    from app.core.config import get_settings
    from app.db.database import SessionLocal
    from app.services.platform.settings_store import load_settings

    settings = get_settings()
    with SessionLocal() as db:
        stored = load_settings(
            db,
            [
                "ai_base_url",
                "ai_api_key",
                "ai_model",
                "context_window_tokens",
                "context_compression_threshold_percent",
                "ai_max_output_tokens",
                "ai_summary_output_tokens",
                "ai_temperature",
                "ai_top_p",
            ],
        )
    return ModelConnectionConfig(
        model_name=stored.get("ai_model") or settings.ai_model,
        base_url=stored.get("ai_base_url") or settings.ai_base_url,
        api_key=stored.get("ai_api_key") or settings.ai_api_key,
        **{
            key: stored[key]
            for key in ("context_window_tokens", "context_compression_threshold_percent")
            if key in stored
        },
        **{
            field: stored[key]
            for key, field in {
                "ai_max_output_tokens": "max_output_tokens",
                "ai_summary_output_tokens": "summary_output_tokens",
                "ai_temperature": "temperature",
                "ai_top_p": "top_p",
            }.items()
            if key in stored
        },
    )


class ModelFactory:
    """Application-owned native clients used across runs on one event loop.

    Configuration changes affect new runs. Existing runs keep their original
    client until they drain, then application shutdown closes all connections.
    Synchronous resolution/construction runs in workers; an async lock prevents
    duplicate cold starts without occupying waiting worker threads. Requests and
    client shutdown stay on the application's event loop. No agent state, tool
    authority or native history is shared here.
    """

    def __init__(self, config_loader: Callable[[], ModelConnectionConfig] = resolve_model_config):
        self.config_loader = config_loader
        self._models: dict[ModelConnectionConfig, OpenAIChatModel] = {}
        self._clients: set[AsyncOpenAI] = set()
        self._closed = False
        self._lock = asyncio.Lock()
        self._close_task: asyncio.Task | None = None

    async def get_model(self, run: dict | None = None) -> OpenAIChatModel:
        if self._closed:
            raise RuntimeError("Model factory is closed")
        if run is None:
            config = await run_db(self.config_loader)
        else:
            snapshot = ModelSnapshot.model_validate(run["model_snapshot"])
            config = await run_db(snapshot.restore)
        return await self._model(config)

    async def _model(self, config: ModelConnectionConfig) -> OpenAIChatModel:
        async with self._lock:
            if self._closed:
                raise RuntimeError("Model factory is closed")
            if config not in self._models:
                return await run_db(self._build, config)
            return self._models[config]

    def _build(self, config: ModelConnectionConfig) -> OpenAIChatModel:
        client = AsyncOpenAI(
            api_key=config.api_key.get_secret_value(),
            base_url=str(config.base_url),
            timeout=config.timeout_seconds,
            max_retries=config.transport_retries,
        )
        # Publish ownership before the await completes, even on cancellation or
        # provider/model construction failure. The async lock drains this worker.
        self._clients.add(client)
        model = OpenAIChatModel(config.model_name, provider=OpenAIProvider(openai_client=client))
        self._models[config] = model
        return model

    async def text(self, *, instructions: str, prompt: str, purpose: str) -> str:
        return _response_text(
            await self._request(instructions=instructions, prompt=prompt, purpose=purpose)
        )

    async def structured(
        self, *, instructions: str, prompt: str, purpose: str, result_type: type[OutputT]
    ) -> OutputT:
        """Extract typed data in one direct request, with no business tool dispatch.

        This output tool is only a transport for data. Ordinary Agent runs still
        finish with natural text. Invalid responses fail without a repair request.
        """
        response = await self._request(
            instructions=instructions,
            prompt=prompt,
            purpose=purpose,
            parameters=ModelRequestParameters(
                output_mode="tool",
                allow_text_output=False,
                output_tools=[
                    ToolDefinition(
                        name="return_result",
                        kind="output",
                        description="Return the requested data. This does not save or execute any business action.",
                        parameters_json_schema=result_type.model_json_schema(),
                    )
                ],
            ),
        )
        calls = [part for part in response.parts if isinstance(part, ToolCallPart)]
        if response.finish_reason in {"length", "content_filter", "error"}:
            raise ValueError(f"Structured request did not complete: {response.finish_reason}")
        if len(calls) != 1 or calls[0].tool_name != "return_result":
            raise ValueError("Structured request must return exactly one result")
        return result_type.model_validate_json(calls[0].args_as_json_str())

    async def _request(
        self,
        *,
        instructions: str,
        prompt: str,
        purpose: str,
        parameters: ModelRequestParameters | None = None,
    ) -> ModelResponse:
        """One native request on the application's loop; no sync bridge or repair.

        Resolve configuration once without holding a caller transaction. Provider
        clients are shared with Agent runs and closed by application shutdown.
        """
        config = await run_db(self.config_loader)
        started = monotonic()
        response = None
        try:
            # Bound the whole one-shot operation, including transport retries;
            # a series of per-attempt timeouts must not keep a save pending forever.
            async with asyncio.timeout(config.timeout_seconds):
                response = await model_request(
                    await self._model(config),
                    [ModelRequest(parts=[SystemPromptPart(instructions), UserPromptPart(prompt)])],
                    model_settings=config.request_settings(),
                    model_request_parameters=parameters,
                )
            return response
        finally:
            logger.info(
                "native_direct_request purpose=%s model=%s seconds=%.3f finish_reason=%s input_tokens=%s output_tokens=%s",
                purpose,
                config.model_name,
                monotonic() - started,
                response.finish_reason if response else None,
                response.usage.input_tokens if response else None,
                response.usage.output_tokens if response else None,
            )

    def snapshot(self) -> dict:
        """Resolve a new run's configuration before its transaction, never on resume."""
        from app.core.security import encrypt_secret

        config = self.config_loader()
        return {
            "schema_version": 1,
            "configuration": config.model_dump(mode="json"),
            "credential": encrypt_secret(config.api_key.get_secret_value()),
        }

    async def close(self) -> None:
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_clients())
        await await_completion(self._close_task)

    async def _close_clients(self) -> None:
        async with self._lock:
            clients = list(self._clients)
            self._clients.clear()
            self._models.clear()
        results = await asyncio.gather(
            *(client.close() for client in clients), return_exceptions=True
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            raise BaseExceptionGroup("Model client shutdown failed", failures)


@asynccontextmanager
async def open_model(config: ModelConnectionConfig) -> AsyncIterator[OpenAIChatModel]:
    """Own one pooled connection for the application's model configuration lifetime.

    The provider is the only transport retry layer. Callers can share the yielded
    model across runs, but not run dependencies or database sessions. Configuration
    changes create a new connection; there is no silent parameter-removal retry.
    """
    factory = ModelFactory(lambda: config)
    try:
        yield await factory.get_model()
    finally:
        await factory.close()


async def request_text(
    model: Model,
    *,
    instructions: str,
    prompt: str,
    model_settings: ModelSettings | None = None,
) -> str:
    """One direct request for titles/summaries; never a hidden secondary agent loop."""
    response = await model_request(
        model,
        [ModelRequest(parts=[SystemPromptPart(instructions), UserPromptPart(prompt)])],
        model_settings=model_settings,
    )
    return _response_text(response)


def _response_text(response) -> str:
    if response.finish_reason in {"length", "content_filter", "error"}:
        raise ValueError(f"Text request did not complete: {response.finish_reason}")
    if not response.text:
        raise ValueError("Text request returned no text")
    return response.text
