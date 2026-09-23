"""Native runtime schema. Lifecycle facts only; no task journal or semantic phases."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from app.db.base import Base

conversations = Table(
    "agent_conversations",
    Base.metadata,
    Column("id", String(128), primary_key=True),
    Column("actor_id", String(128), nullable=False),
    Column("title", String(500), nullable=False, default="New conversation"),
    Column("scene", JSON, nullable=False, default=dict),
    Column("created_at", Float, nullable=False, default=0),
    Column("next_run_seq", Integer, nullable=False, default=0),
    Column("active_run_id", String(64)),
)

runs = Table(
    "agent_runs",
    Base.metadata,
    Column("id", String(64), primary_key=True),
    Column("conversation_id", ForeignKey("agent_conversations.id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("client_request_id", String(128), nullable=False),
    Column("input_fingerprint", String(64), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("prompt", Text, nullable=False),
    Column("definition", JSON, nullable=False),
    Column("model_snapshot", JSON),
    Column("status", String(32), nullable=False),
    Column("owner_id", String(64)),
    Column("lease_until", Float),
    Column("cancel_requested", Boolean, nullable=False, default=False),
    Column("message_offset", Integer),
    Column("event_seq", Integer, nullable=False, default=0),
    Column("budget", JSON, nullable=False),
    Column("pending", JSON),
    Column("output", Text),
    Column("error_code", String(80)),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("conversation_id", "seq"),
    UniqueConstraint("conversation_id", "client_request_id"),
)

messages = Table(
    "agent_messages",
    Base.metadata,
    Column("run_id", ForeignKey("agent_runs.id"), primary_key=True),
    Column("index", Integer, primary_key=True),
    Column("payload", JSON, nullable=False),
    Column("sdk_version", String(32), nullable=False, default="2.43.0"),
)

tool_calls = Table(
    "agent_tool_calls",
    Base.metadata,
    Column("run_id", ForeignKey("agent_runs.id"), primary_key=True),
    Column("call_id", String(256), primary_key=True),
    Column("name", String(256), nullable=False),
    Column("arguments", JSON, nullable=False),
    Column("target", JSON, nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("mutating", Boolean, nullable=False),
    Column("resource_key", String(512)),
    Column("status", String(32), nullable=False),
    Column("result", JSON),
)

approvals = Table(
    "agent_approvals",
    Base.metadata,
    Column("run_id", ForeignKey("agent_runs.id"), primary_key=True),
    Column("call_id", String(256), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("decision", String(32), nullable=False),
    Column("decided_by", String(128)),
    Column("decided_at", Float),
)

events = Table(
    "agent_run_events",
    Base.metadata,
    Column("run_id", ForeignKey("agent_runs.id"), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("kind", String(80), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", Float, nullable=False),
)

resource_locks = Table(
    "agent_resource_locks",
    Base.metadata,
    Column("resource_key", String(512), primary_key=True),
    Column("run_id", ForeignKey("agent_runs.id"), nullable=False),
    Column("call_id", String(256), nullable=False),
)

context_snapshots = Table(
    "agent_context_snapshots",
    Base.metadata,
    Column("id", String(64), primary_key=True),
    Column("conversation_id", ForeignKey("agent_conversations.id"), nullable=False),
    Column("run_id", ForeignKey("agent_runs.id"), nullable=False),
    Column("source_indices", JSON, nullable=False),
    Column("source_fingerprint", String(64), nullable=False),
    Column("summary", Text, nullable=False),
    Column("references", JSON, nullable=False),
    Column("model_name", String(256), nullable=False),
    Column("prompt_version", String(32), nullable=False),
    Column("created_at", Float, nullable=False),
)

RUNTIME_TABLES = [
    conversations,
    runs,
    messages,
    tool_calls,
    approvals,
    events,
    resource_locks,
    context_snapshots,
]
