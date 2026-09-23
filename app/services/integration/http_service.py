"""Provider-neutral HTTP runtime for registered external Services."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.logging import fmt_kv, get_logger

logger = get_logger("integration.http_service")


@dataclass(frozen=True)
class ServiceHTTPError(Exception):
    code: str
    message: str
    http_status: int | None = None
    response: Any | None = None
    outcome_unknown: bool = False

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.http_status is not None:
            result["http_status"] = self.http_status
        if self.response is not None:
            result["response"] = self.response
        return result


def public_service_config(raw_config: dict[str, Any] | None) -> dict[str, Any]:
    """Return non-secret connection settings for API responses."""
    return dict(raw_config or {})


def validate_request_path(path: str) -> str:
    normalized = str(path or "").strip()
    parsed = urlsplit(normalized)
    if (
        not normalized.startswith("/")
        or normalized.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
    ):
        raise ServiceHTTPError(
            code="invalid_path",
            message="Service path must be a relative API path beginning with /",
        )
    return normalized


def validate_request_body(method: str, body: Any | None) -> None:
    if body is not None and method not in {"POST", "PUT", "PATCH"}:
        raise ServiceHTTPError(
            code="invalid_body",
            message="This transport accepts a JSON body only for POST, PUT or PATCH; no fields are silently discarded.",
        )


def _build_headers(config: dict[str, Any], secrets: dict[str, Any]) -> dict[str, str]:
    headers = {str(key): str(value) for key, value in (config.get("default_headers") or {}).items()}
    headers.update({str(key): str(value) for key, value in (secrets.get("headers") or {}).items()})
    auth_type = str(config.get("auth_type") or "none")
    if auth_type == "bearer" and secrets.get("bearer_token"):
        headers["Authorization"] = f"Bearer {secrets['bearer_token']}"
    elif auth_type == "api_key" and secrets.get("api_key"):
        headers[str(config.get("api_key_header") or "X-API-Key")] = str(secrets["api_key"])
    return headers


def _parse_response(
    response: httpx.Response,
    *,
    raw: bytes,
    response_format: str,
    max_response_bytes: int,
) -> Any:
    if len(raw) > max_response_bytes:
        preview = raw[:max_response_bytes].decode(response.encoding or "utf-8", errors="replace")
        return {
            "truncated": True,
            "total_bytes": None,
            "observed_bytes_at_least": len(raw),
            "response_preview": preview,
        }

    content_type = str(response.headers.get("content-type") or "").lower()
    should_parse_json = response_format == "json" or (
        response_format == "auto" and ("json" in content_type or not raw)
    )
    if should_parse_json:
        try:
            return json.loads(raw) if raw else None
        except Exception as exc:
            raise ServiceHTTPError(
                code="unexpected_response_format",
                message="Service returned non-JSON content while JSON was required.",
                http_status=response.status_code,
                response={
                    "content_type": content_type or None,
                    "preview": raw.decode(response.encoding or "utf-8", errors="replace")[:500],
                },
                outcome_unknown=True,
            ) from exc
    if response_format == "auto":
        try:
            return json.loads(raw)
        except Exception:
            pass
    return raw.decode(response.encoding or "utf-8", errors="replace")


def _redact_response(value: Any, secrets: dict[str, Any]) -> Any:
    """Do not expose stored credentials when an upstream echoes headers or errors."""
    values = []

    def collect(item):
        if isinstance(item, dict):
            for child in item.values():
                collect(child)
        elif isinstance(item, str) and item:
            values.append(item)

    collect(secrets)
    if secrets.get("username") is not None and secrets.get("password") is not None:
        values.append(
            base64.b64encode(f"{secrets['username']}:{secrets['password']}".encode()).decode()
        )

    def redact(item):
        if isinstance(item, str):
            for secret in sorted(set(values), key=len, reverse=True):
                item = item.replace(secret, "[redacted]")
            return item
        if isinstance(item, dict):
            return {redact(key): redact(child) for key, child in item.items()}
        if isinstance(item, list):
            return [redact(child) for child in item]
        return item

    return redact(value)


async def call_http_service(
    *,
    service_id: int | None,
    service_type: str,
    raw_config: dict[str, Any] | None,
    raw_secrets: dict[str, Any] | None,
    method: str,
    path: str,
    query_params: dict[str, Any] | None = None,
    body: Any | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Call one registered HTTP API and return a bounded structured result."""
    config = dict(raw_config or {})
    secrets = dict(raw_secrets or {})
    base_url = str(config.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ServiceHTTPError(code="invalid_config", message="Service base_url is missing")
    request_path = validate_request_path(path)
    method_upper = str(method or "").strip().upper()
    if method_upper not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ServiceHTTPError(code="invalid_method", message=f"Unsupported method: {method}")
    validate_request_body(method_upper, body)

    auth: httpx.BasicAuth | None = None
    if config.get("auth_type") == "basic":
        auth = httpx.BasicAuth(
            str(secrets.get("username") or ""),
            str(secrets.get("password") or ""),
        )
    headers = _build_headers(config, secrets)
    url = f"{base_url}{request_path}"
    timeout = float(config.get("timeout_seconds") or 30.0)
    verify_tls = bool(config.get("verify_tls", True))
    max_response_bytes = int(config.get("max_response_bytes") or 262_144)
    use_environment_proxy = bool(config.get("use_environment_proxy", False))
    create_client = client_factory or httpx.AsyncClient
    logger.info(
        "service_http_call_start %s",
        fmt_kv(
            service_id=service_id, service_type=service_type, method=method_upper, path=request_path
        ),
    )
    try:
        async with create_client(
            timeout=timeout,
            verify=verify_tls,
            trust_env=use_environment_proxy,
            follow_redirects=False,
        ) as client:
            async with client.stream(
                method_upper,
                url,
                params=query_params,
                json=body if method_upper in {"POST", "PUT", "PATCH"} else None,
                auth=auth,
                headers=headers,
            ) as response:
                # Bound the decoded stream while reading, not after buffering an
                # arbitrary response. Stop once truncation can be established.
                raw = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=16_384):
                    raw.extend(chunk[: max_response_bytes + 1 - len(raw)])
                    if len(raw) > max_response_bytes:
                        break
    except httpx.TimeoutException as exc:
        logger.warning(
            "service_http_call_timeout %s",
            fmt_kv(service_id=service_id, method=method_upper, path=request_path),
        )
        raise ServiceHTTPError(
            code="timeout",
            message=f"Request timed out after {timeout:g} seconds",
            outcome_unknown=not isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout)),
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning(
            "service_http_call_connection_error %s",
            fmt_kv(service_id=service_id, method=method_upper, path=request_path),
        )
        raise ServiceHTTPError(
            code="connection_error",
            message="HTTP transport failed",
            outcome_unknown=not isinstance(exc, httpx.ConnectError),
        ) from exc
    except (ImportError, OSError, ValueError) as exc:
        logger.warning(
            "service_http_call_client_error %s",
            fmt_kv(service_id=service_id, method=method_upper, path=request_path),
        )
        raise ServiceHTTPError(
            code="client_configuration_error",
            message="HTTP client configuration failed",
            outcome_unknown=True,
        ) from exc

    response_format = str(config.get("response_format") or "auto")
    try:
        parsed = _redact_response(
            _parse_response(
                response,
                raw=bytes(raw),
                response_format=response_format,
                max_response_bytes=max_response_bytes,
            ),
            secrets,
        )
    except ServiceHTTPError as exc:
        raise ServiceHTTPError(
            exc.code,
            exc.message,
            exc.http_status,
            _redact_response(exc.response, secrets),
            exc.outcome_unknown,
        ) from exc
    if response.status_code >= 300:
        logger.warning(
            "service_http_call_api_error %s",
            fmt_kv(service_id=service_id, http_status=response.status_code, path=request_path),
        )
        raise ServiceHTTPError(
            code="api_error",
            message=f"Service returned HTTP {response.status_code}",
            http_status=response.status_code,
            response=parsed,
            outcome_unknown=response.status_code >= 500 or response.status_code == 408,
        )

    logger.info(
        "service_http_call_success %s",
        fmt_kv(service_id=service_id, http_status=response.status_code, path=request_path),
    )
    return {
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "data": parsed,
    }


def config_for_storage(config: Any) -> dict[str, Any]:
    """Convert a Pydantic config or mapping into JSON-compatible storage."""
    if hasattr(config, "model_dump"):
        return config.model_dump()
    return json.loads(json.dumps(config))
