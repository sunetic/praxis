"""MCP HTTP transports and durable run-scoped toolsets."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx2
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from pydantic_ai import RunContext, Tool
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import get_encryption_key
from app.models.models import Service
from app.services.agent.definitions import RunDependencies
from app.services.agent.execution import DurableToolset, RegisteredTool, ToolAccess
from app.services.agent.persistence import run_db
from app.services.agent.store import RunStore
from app.services.integration.agent_tools import bound_service_ids

MCP_SERVICE_TYPE = "mcp"
MCP_TRANSPORTS = frozenset({"streamable_http", "sse"})


@dataclass(frozen=True)
class MCPConnectionResult:
    tools: tuple[str, ...]


@dataclass(frozen=True)
class MCPServiceSnapshot:
    service_id: int
    name: str
    config: dict[str, Any]
    secrets: dict[str, Any]
    target: dict[str, Any]


class MCPServiceError(Exception):
    """A safe user-visible MCP configuration or connection error."""


def _headers_and_auth(
    config: dict[str, Any], secrets: dict[str, Any]
) -> tuple[dict[str, str], httpx2.Auth | None]:
    headers = {str(key): str(value) for key, value in (config.get("default_headers") or {}).items()}
    headers.update({str(key): str(value) for key, value in (secrets.get("headers") or {}).items()})
    auth_type = str(config.get("auth_type") or "none")
    auth: httpx2.Auth | None = None
    if auth_type == "basic":
        auth = httpx2.BasicAuth(
            str(secrets.get("username") or ""),
            str(secrets.get("password") or ""),
        )
    elif auth_type == "bearer" and secrets.get("bearer_token"):
        headers["Authorization"] = f"Bearer {secrets['bearer_token']}"
    elif auth_type == "api_key" and secrets.get("api_key"):
        headers[str(config.get("api_key_header") or "X-API-Key")] = str(secrets["api_key"])
    return headers, auth


def _http_client_factory(
    config: dict[str, Any],
) -> Callable[..., httpx2.AsyncClient]:
    verify = bool(config.get("verify_tls", True))
    trust_env = bool(config.get("use_environment_proxy", False))

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **kwargs: Any,
    ) -> httpx2.AsyncClient:
        options: dict[str, Any] = {
            "headers": headers,
            "timeout": timeout or httpx2.Timeout(30.0, read=300.0),
            "verify": verify,
            "trust_env": trust_env,
            "follow_redirects": bool(kwargs.get("follow_redirects", False)),
        }
        if auth is not None:
            options["auth"] = auth
        return httpx2.AsyncClient(**options)

    return factory


def build_mcp_toolset(
    *,
    service_id: int | None,
    config: dict[str, Any],
    secrets: dict[str, Any] | None,
) -> MCPToolset[RunDependencies]:
    url = str(config.get("base_url") or "").strip()
    transport_name = str(config.get("mcp_transport") or "")
    if not url:
        raise MCPServiceError("MCP endpoint URL is missing")
    if transport_name not in MCP_TRANSPORTS:
        raise MCPServiceError("MCP transport must be streamable_http or sse")
    timeout = float(config.get("timeout_seconds") or 30.0)
    headers, auth = _headers_and_auth(config, dict(secrets or {}))
    transport_type = SSETransport if transport_name == "sse" else StreamableHttpTransport
    transport = transport_type(
        url,
        headers=headers,
        auth=auth,
        httpx_client_factory=_http_client_factory(config),
    )
    return MCPToolset(
        transport,
        id=f"mcp-service-{service_id or 'preview'}",
        tool_error_behavior="failed",
        init_timeout=timeout,
        read_timeout=timeout,
    )


def _redact_error(exc: Exception, secrets: dict[str, Any] | None) -> str:
    message = str(exc).strip() or type(exc).__name__
    values: list[str] = []
    for value in (secrets or {}).values():
        if isinstance(value, str) and value:
            values.append(value)
        elif isinstance(value, dict):
            values.extend(str(item) for item in value.values() if item)
    for value in sorted(set(values), key=len, reverse=True):
        message = message.replace(value, "[redacted]")
    return message[:500]


async def test_mcp_connection(
    *,
    service_id: int | None,
    config: dict[str, Any],
    secrets: dict[str, Any] | None,
) -> MCPConnectionResult:
    try:
        toolset = build_mcp_toolset(
            service_id=service_id,
            config=config,
            secrets=secrets,
        )
        async with toolset:
            tools = await toolset.list_tools()
    except MCPServiceError:
        raise
    except Exception as exc:
        raise MCPServiceError(f"MCP connection failed: {_redact_error(exc, secrets)}") from exc
    return MCPConnectionResult(tuple(sorted(tool.name for tool in tools)))


def _target(service: Service) -> dict[str, Any]:
    config = dict(service.config or {})
    secrets = dict(service.secrets or {})
    digest = hmac.new(
        get_encryption_key(),
        json.dumps(
            {
                "config": config,
                "secrets": secrets,
                "status": service.status,
                "type": service.service_type,
                "binding": service.resource_ref,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "service_id": service.id,
        "base_url": config.get("base_url"),
        "transport": config.get("mcp_transport"),
        "resource_ref": service.resource_ref,
        "configuration_revision": digest,
    }


def _snapshot(service: Service) -> MCPServiceSnapshot:
    return MCPServiceSnapshot(
        service_id=service.id,
        name=service.name,
        config=dict(service.config or {}),
        secrets=dict(service.secrets or {}),
        target=_target(service),
    )


class MCPRunToolsets:
    """Build isolated MCP clients from the exact Service snapshot for one run."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        store: RunStore,
        confirmation_bypass: Callable[[], bool] | None,
    ) -> None:
        self.sessions = sessions
        self.store = store
        self.confirmation_bypass = confirmation_bypass

    def _current_target(
        self, service_id: int, ctx: RunContext[RunDependencies]
    ) -> dict[str, Any] | None:
        if ctx.deps.actor_id != "local" or service_id not in set(
            ctx.deps.scope.get("mcp_service_ids", [])
        ):
            return None
        with self.sessions() as db:
            datasource_ids = set(ctx.deps.scope.get("datasource_ids", []))
            if service_id not in bound_service_ids(db, datasource_ids):
                return None
            service = db.get(Service, service_id)
            if (
                service is None
                or service.status != "active"
                or service.service_type != MCP_SERVICE_TYPE
            ):
                return None
            return _target(service)

    def __call__(
        self, row: dict[str, Any], deps: RunDependencies, owner_id: str
    ) -> list[AbstractToolset[RunDependencies]]:
        service_ids = tuple(dict.fromkeys(deps.scope.get("mcp_service_ids", [])))
        if not service_ids:
            return []
        with self.sessions() as db:
            authorized_ids = set(service_ids) & bound_service_ids(
                db, set(deps.scope.get("datasource_ids", []))
            )
            records = {
                service.id: _snapshot(service)
                for service in db.query(Service).filter(Service.id.in_(authorized_ids)).all()
                if service.status == "active" and service.service_type == MCP_SERVICE_TYPE
            }
        result: list[AbstractToolset[RunDependencies]] = []
        for service_id in service_ids:
            snapshot = records.get(service_id)
            if snapshot is None:
                continue
            expected_target = snapshot.target

            async def authorize(
                ctx: RunContext[RunDependencies],
                _args: dict[str, Any],
                *,
                selected_id: int = service_id,
                expected: dict[str, Any] = expected_target,
            ) -> ToolAccess:
                current = await run_db(self._current_target, selected_id, ctx)
                return ToolAccess(
                    allowed=current == expected,
                    target=current or {"service_id": selected_id},
                    requires_approval=True,
                    resource_key=f"mcp-service:{selected_id}",
                )

            async def placeholder() -> None:
                raise RuntimeError("MCP placeholder must never execute")

            entry = RegisteredTool(
                tool=Tool(
                    placeholder,
                    name=f"_mcp_service_{service_id}",
                    sequential=True,
                ),
                authorize=authorize,
                mutating=True,
                timeout_seconds=float(snapshot.config.get("timeout_seconds") or 30.0),
            )
            remote = build_mcp_toolset(
                service_id=service_id,
                config=snapshot.config,
                secrets=snapshot.secrets,
            ).prefixed(f"mcp_{service_id}")
            result.append(
                DurableToolset(
                    remote,
                    store=self.store,
                    run_id=row["id"],
                    owner_id=owner_id,
                    registered={},
                    confirmation_bypass=self.confirmation_bypass,
                    default_entry=entry,
                )
            )
        return result
