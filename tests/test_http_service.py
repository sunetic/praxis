from __future__ import annotations

import httpx
import pytest

from app.services.integration.http_service import ServiceHTTPError, call_http_service


def _client_factory(handler):
    transport = httpx.MockTransport(handler)

    def create_client(**kwargs):
        kwargs.pop("verify", None)
        return httpx.AsyncClient(transport=transport, **kwargs)

    return create_client


@pytest.mark.anyio
async def test_generic_http_service_calls_json_api_with_bearer_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query"
        assert request.url.params["query"] == "up"
        assert request.headers["authorization"] == "Bearer secret-token"
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"status": "success", "data": {"result": []}},
        )

    result = await call_http_service(
        service_id=7,
        service_type="prometheus",
        raw_config={
            "base_url": "http://prometheus:9090",
            "auth_type": "bearer",
            "response_format": "auto",
        },
        raw_secrets={"bearer_token": "secret-token"},
        method="GET",
        path="/api/v1/query",
        query_params={"query": "up"},
        client_factory=_client_factory(handler),
    )

    assert result["http_status"] == 200
    assert result["data"]["status"] == "success"


@pytest.mark.anyio
async def test_generic_http_service_accepts_text_when_response_format_is_auto() -> None:
    result = await call_http_service(
        service_id=None,
        service_type="http_api",
        raw_config={
            "base_url": "http://service.internal",
            "response_format": "auto",
        },
        raw_secrets=None,
        method="GET",
        path="/-/ready",
        client_factory=_client_factory(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/plain"}, text="Prometheus is Ready."
            )
        ),
    )

    assert result["data"] == "Prometheus is Ready."


@pytest.mark.anyio
async def test_generic_http_service_rejects_absolute_request_path() -> None:
    with pytest.raises(ServiceHTTPError, match="relative API path") as exc_info:
        await call_http_service(
            service_id=1,
            service_type="http_api",
            raw_config={"base_url": "http://service.internal"},
            raw_secrets=None,
            method="GET",
            path="https://example.com/steal",
        )

    assert exc_info.value.code == "invalid_path"


@pytest.mark.anyio
async def test_generic_http_service_returns_structured_api_error() -> None:
    with pytest.raises(ServiceHTTPError) as exc_info:
        await call_http_service(
            service_id=3,
            service_type="http_api",
            raw_config={"base_url": "http://service.internal", "response_format": "json"},
            raw_secrets=None,
            method="GET",
            path="/missing",
            client_factory=_client_factory(
                lambda request: httpx.Response(404, json={"error": "not found"})
            ),
        )

    assert exc_info.value.code == "api_error"
    assert exc_info.value.http_status == 404
    assert exc_info.value.response == {"error": "not found"}


@pytest.mark.anyio
async def test_generic_http_service_disables_environment_proxy_by_default() -> None:
    captured: dict[str, object] = {}

    def create_client(**kwargs):
        captured.update(kwargs)
        kwargs.pop("verify", None)
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text="ok")),
            **kwargs,
        )

    await call_http_service(
        service_id=1,
        service_type="http_api",
        raw_config={"base_url": "http://service.internal"},
        raw_secrets=None,
        method="GET",
        path="/health",
        client_factory=create_client,
    )

    assert captured["trust_env"] is False


@pytest.mark.anyio
async def test_stream_is_bounded_before_buffering_and_closed() -> None:
    class LargeBody(httpx.AsyncByteStream):
        count = 0
        closed = False

        async def __aiter__(self):
            for _ in range(100):
                self.count += 1
                yield b"x" * 16384

        async def aclose(self):
            self.closed = True

    body = LargeBody()
    result = await call_http_service(
        service_id=1,
        service_type="http_api",
        raw_secrets=None,
        raw_config={"base_url": "http://fixture.invalid", "max_response_bytes": 1024},
        method="GET",
        path="/large",
        client_factory=_client_factory(lambda request: httpx.Response(200, stream=body)),
    )
    assert body.count == 1 and body.closed
    assert result["data"]["truncated"] is True
    assert len(result["data"]["response_preview"]) == 1024
    assert result["data"]["total_bytes"] is None


@pytest.mark.anyio
async def test_redirect_does_not_send_stored_credentials_to_another_host() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://another.invalid/steal"})

    with pytest.raises(ServiceHTTPError) as caught:
        await call_http_service(
            service_id=1,
            service_type="http_api",
            raw_secrets={"bearer_token": "sensitive-token"},
            raw_config={"base_url": "http://fixture.invalid", "auth_type": "bearer"},
            method="GET",
            path="/redirect",
            client_factory=_client_factory(handler),
        )
    assert len(requests) == 1 and caught.value.http_status == 302


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["GET", "DELETE"])
async def test_unsupported_body_is_not_silently_discarded(method) -> None:
    requests = []
    with pytest.raises(ServiceHTTPError) as caught:
        await call_http_service(
            service_id=1,
            service_type="http_api",
            raw_secrets=None,
            raw_config={"base_url": "http://fixture.invalid"},
            method=method,
            path="/item",
            body={"id": "item"},
            client_factory=_client_factory(
                lambda request: requests.append(request) or httpx.Response(200)
            ),
        )
    assert caught.value.code == "invalid_body" and not caught.value.outcome_unknown
    assert not requests
