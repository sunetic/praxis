"""Typed database tools for the native agent; no legacy registry or pending actions."""

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from typing import Annotated

from pydantic import Field, TypeAdapter
from pydantic_ai import ModelRetry, RunContext, Tool
from pydantic_ai.exceptions import ToolFailed
from sqlglot import exp, parse
from sqlglot.errors import SqlglotError

from app.core.security import get_encryption_key
from app.db.pool_factory import get_pool_for_datasource
from app.models.models import Agent, DataSource
from app.services.agent.definitions import RunDependencies
from app.services.agent.execution import RegisteredTool, ToolAccess
from app.services.agent.persistence import run_db
from app.services.agent.store import OutcomeUnknownError, RunStore
from app.services.datasource.write_executor import (
    WriteNotExecutedError,
    WriteOutcomeUnknownError,
    execute_write,
    validate_write_sql,
)
from app.services.platform.settings_store import get_setting

DATABASE_TOOLS = frozenset({"list_datasources", "query_database", "request_database_change"})


def validate_read_sql(sql: str, dialect: str) -> None:
    """Syntax boundary, backed by a read-only DB transaction (not a safety claim by the LLM)."""
    if re.search(r"/\*(?:!|M!)", sql, re.IGNORECASE):
        raise ToolFailed("Executable SQL comments are not supported by this read tool.")
    try:
        statements = parse(sql, read=dialect)
    except SqlglotError as exc:
        raise ToolFailed("SQL could not be parsed; provide one read-only query.") from exc
    if len(statements) != 1:
        raise ToolFailed(
            f"query_database accepts exactly one SQL statement per call, but received "
            f"{len(statements)}. Submit each read-only SELECT/WITH query, SHOW, or DESCRIBE "
            "as a separate query_database call."
        )
    if not isinstance(statements[0], (exp.Query, exp.Show, exp.Describe)):
        raise ToolFailed(
            "query_database accepts exactly one read-only SELECT/WITH query, SHOW, or "
            "DESCRIBE statement. Use request_database_change for writes or schema changes."
        )
    if any(
        isinstance(node, (exp.DML, exp.DDL, exp.Into, exp.Lock)) for node in statements[0].walk()
    ):
        raise ToolFailed(
            "Writes, SELECT INTO, and locking clauses are not authorized by this read tool."
        )
    if any(isinstance(node, exp.Anonymous) for node in statements[0].walk()):
        # An arbitrary stored routine/UDF may affect external resources even in
        # a read-only transaction. Only parser-recognized built-ins are accepted.
        raise ToolFailed(
            "Custom or unrecognized SQL functions are not available in the read-only tool."
        )


def database_tools(session_factory) -> dict[str, RegisteredTool]:
    store = RunStore(session_factory)

    def authorized_ids(ctx):
        if ctx.deps.actor_id != "local":
            return set()
        ids = set(ctx.deps.scope.get("datasource_ids", []))
        agent_id = ctx.deps.scope.get("agent_id")
        if agent_id is not None:
            with session_factory() as db:
                agent = db.get(Agent, agent_id)
                if (
                    agent is None
                    or agent.status != "active"
                    or ctx.tool_name not in (agent.tools or [])
                ):
                    return set()
                ids &= {item.id for item in agent.datasources}
        return ids

    def resolve(ctx, datasource_id):
        if datasource_id not in authorized_ids(ctx):
            return None
        with session_factory() as db:
            datasource = db.get(DataSource, datasource_id)
            if datasource is None or datasource.status != "active":
                return None
            db.expunge(datasource)
            return datasource

    async def authorize_list(ctx, _args):
        return ToolAccess(
            allowed=True, target={"datasource_ids": sorted(await run_db(authorized_ids, ctx))}
        )

    async def authorize_query(ctx, args):
        datasource = await run_db(resolve, ctx, args.get("datasource_id"))
        if datasource is None:
            return ToolAccess(allowed=False, target={"datasource_id": args.get("datasource_id")})
        return ToolAccess(
            allowed=True,
            target={
                "datasource_id": datasource.id,
                "database": datasource.database,
                "host": datasource.host,
                "port": datasource.port,
                "user": datasource.user,
                "revision": datasource.updated_at.isoformat() if datasource.updated_at else None,
            },
        )

    def write_snapshot(ctx, datasource_id):
        datasource = resolve(ctx, datasource_id)
        if datasource is None:
            return None
        with session_factory() as db:
            if get_setting(db, "sql_allow_mutating") is not True:
                return None
        connection = {
            key: getattr(datasource, key)
            for key in (
                "host",
                "port",
                "db_type",
                "database",
                "user",
                "password",
                "access_level",
                "tenant_role",
                "cluster_key",
            )
        }
        digest = hmac.new(
            get_encryption_key(),
            json.dumps(connection, sort_keys=True).encode(),
            hashlib.sha256,
        ).hexdigest()
        target = {
            "datasource_id": datasource.id,
            "host": datasource.host,
            "port": datasource.port,
            "database": datasource.database,
            "user": datasource.user,
            "engine": datasource.db_type,
            "access_level": datasource.access_level,
            "configuration_revision": digest,
        }
        # Credentials/aliases to the same configured database share its fence.
        resource = [datasource.host, datasource.port, datasource.database]
        resource_key = "database:" + hashlib.sha256(json.dumps(resource).encode()).hexdigest()
        return datasource, target, resource_key

    async def authorize_write(ctx, args):
        snapshot = await run_db(write_snapshot, ctx, args.get("datasource_id"))
        return ToolAccess(
            allowed=snapshot is not None,
            target=snapshot[1] if snapshot else {"datasource_id": args.get("datasource_id")},
            requires_approval=True,
            resource_key=snapshot[2] if snapshot else None,
        )

    async def validate_write(ctx, **args):
        datasource = await run_db(resolve, ctx, args["datasource_id"])
        if datasource is None:
            return  # Authorization reports the denied resource, not a syntax retry.
        dialect = "postgres" if datasource.db_type in {"postgres", "postgresql"} else "mysql"
        try:
            validate_write_sql(args["sql"], dialect)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc

    def list_datasources(ctx: RunContext[RunDependencies]) -> list[dict]:
        """List the active databases authorized for this run, with IDs and database engines."""
        with session_factory() as db:
            records = (
                db.query(DataSource)
                .filter(
                    DataSource.id.in_(authorized_ids(ctx)),
                    DataSource.status == "active",
                )
                .order_by(DataSource.id)
                .all()
            )
            return [
                {
                    "id": item.id,
                    "name": item.name,
                    "engine": item.db_type,
                    "database": item.database,
                    "access_level": item.access_level,
                    "platform_writes_enabled": get_setting(db, "sql_allow_mutating") is True,
                    "writes_require_approval": True,
                }
                for item in records
            ]

    async def query_database(
        ctx: RunContext[RunDependencies],
        datasource_id: Annotated[int, Field(gt=0)],
        sql: Annotated[
            str,
            Field(
                min_length=1,
                max_length=100_000,
                description=(
                    "Exactly one SQL statement: a read-only SELECT/WITH query, SHOW, or "
                    "DESCRIBE. Never combine statements with semicolons; call "
                    "query_database separately for each statement."
                ),
            ),
        ],
        limit: Annotated[int, Field(ge=1, le=1000)] = 200,
    ) -> dict:
        """Run exactly one SQL statement against an authorized database, read-only.

        Submit one SELECT/WITH query, SHOW, or DESCRIBE per call. Never put
        multiple statements in one ``sql`` argument, even when every statement
        is read-only; make separate ``query_database`` calls instead. Results
        include a bounded preview and explicit truncation. If truncated, narrow
        the query or use SQL pagination; never assume the preview is the full
        set. SQL errors are returned to you so you can correct the query. This
        tool cannot execute writes, even if the user has approved a different
        action. Custom routines and executable comments are not supported.
        """
        datasource = await run_db(resolve, ctx, datasource_id)
        if datasource is None:
            raise ToolFailed("Datasource is not currently authorized or active.")
        dialect = "postgres" if datasource.db_type in {"postgres", "postgresql"} else "mysql"
        validate_read_sql(sql, dialect)
        pool = get_pool_for_datasource(datasource)
        try:
            result = await pool.execute_read_query(datasource, sql, limit=limit)
        except Exception as exc:
            detail = str(exc)
            if datasource.password:
                detail = detail.replace(datasource.password, "[redacted]")
            raise ToolFailed(f"Database query failed: {detail[:2000]}") from exc
        return TypeAdapter(dict).dump_python(
            {
                "datasource_id": datasource.id,
                "database": datasource.database,
                "observed_at": datetime.now(UTC).isoformat(),
                **result,
            },
            mode="json",
        )

    async def request_database_change(
        ctx: RunContext[RunDependencies],
        datasource_id: Annotated[int, Field(gt=0)],
        sql: Annotated[str, Field(min_length=1, max_length=100_000)],
    ) -> dict:
        """Request approval for one SQL data/schema change on an authorized database.

        Call this tool to create the platform approval request; SQL is sent only
        after the user approves it. Writing SQL in chat does not create an approval
        request. Requires platform writes enabled. Approval covers exact SQL and
        connection; this tool never switches to other/admin credentials. Supports
        data/schema changes and GRANT/REVOKE, not scripts, session/transaction
        control or stored procedures. No retries. Returns a server completion
        receipt, not RETURNING rows or proof of the user's overall goal. Use the
        read tool for verification. A write cannot be assumed undone on error or
        cancellation; unknown effects require reconciliation before another write.
        """
        snapshot = await run_db(write_snapshot, ctx, datasource_id)
        if snapshot is None:
            raise ToolFailed("Database write permission is unavailable; no SQL was sent.")
        datasource, target, _ = snapshot
        row = await run_db(store.get, ctx.deps.run_id, ctx.deps.actor_id)
        call = next(item for item in row["tool_calls"] if item["call_id"] == ctx.tool_call_id)
        if target != call["target"]:
            raise ToolFailed("Database connection changed after approval; no SQL was sent.")
        approval = next(item for item in row["approvals"] if item["call_id"] == ctx.tool_call_id)
        if approval["decision"] != "approved" or call["status"] != "executing":
            raise ToolFailed("Database write has no active approved dispatch; no SQL was sent.")
        receipt = {
            key: approval[key] for key in ("call_id", "decision", "decided_by", "decided_at")
        }
        try:
            result = await execute_write(datasource, sql)
        except WriteNotExecutedError as exc:
            failure = {
                "outcome": "not_executed",
                "datasource_id": datasource.id,
                "database": datasource.database,
                "observed_at": datetime.now(UTC).isoformat(),
                "execution_stage": exc.stage,
                "business_statement_dispatched": False,
                "database_error": str(exc),
                "cause": "not_determined_by_this_receipt",
            }
            raise ToolFailed(json.dumps(failure, ensure_ascii=False)) from exc
        except WriteOutcomeUnknownError as exc:
            raise OutcomeUnknownError(
                "Database write outcome is unknown; reconcile before retrying.",
                details={"database_error": str(exc), "approval": receipt},
            ) from exc
        return {
            "datasource_id": datasource_id,
            "database": datasource.database,
            "approval": receipt,
            "observed_at": datetime.now(UTC).isoformat(),
            **result,
        }

    return {
        "list_datasources": RegisteredTool(tool=Tool(list_datasources), authorize=authorize_list),
        "query_database": RegisteredTool(tool=Tool(query_database), authorize=authorize_query),
        "request_database_change": RegisteredTool(
            tool=Tool(request_database_change, sequential=True, args_validator=validate_write),
            authorize=authorize_write,
            mutating=True,
        ),
    }
