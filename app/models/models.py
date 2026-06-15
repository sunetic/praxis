import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import EncryptedString
from app.db.database import Base


class DataSource(Base):
    __tablename__ = "datasources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    db_type: Mapped[str] = mapped_column(String(50), default="mysql")
    cluster_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    tenant_role: Mapped[str] = mapped_column(String(50), nullable=False, default="user")

    user: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    password: Mapped[Optional[str]] = mapped_column(EncryptedString, nullable=True)
    database: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    attributes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(String(50), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    conversations: Mapped[List["Conversation"]] = relationship(
        back_populates="datasource", cascade="all, delete-orphan"
    )
    agents: Mapped[List["Agent"]] = relationship(
        secondary="agent_datasources", back_populates="datasources"
    )
    schedules: Mapped[List["Schedule"]] = relationship(back_populates="datasource")


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="dingtalk")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(500), default="New Conversation")
    datasource_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("datasources.id"), nullable=True
    )
    agent_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agents.id"), nullable=True)
    active_skills: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="primary")
    scene_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    datasource: Mapped[Optional["DataSource"]] = relationship(back_populates="conversations")
    agent: Mapped[Optional["Agent"]] = relationship(back_populates="conversations")
    messages: Mapped[List["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    chat_events: Mapped[List["ChatEvent"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    pending_actions: Mapped[List["PendingAction"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    build_sessions: Mapped[List["BuildSession"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tool_calls: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    content_parts: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class ChatEvent(Base):
    __tablename__ = "chat_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    phase: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    turn_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    turn_seq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    part_seq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    role: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    agent_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped["Conversation"] = relationship(back_populates="chat_events")


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id"), nullable=False
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False, default="execute_sql")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="pending_actions")


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    tools: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)
    skills: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False, default="custom")
    status: Mapped[str] = mapped_column(String(50), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    conversations: Mapped[List["Conversation"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    datasources: Mapped[List["DataSource"]] = relationship(
        secondary="agent_datasources", back_populates="agents"
    )
    tool_executions: Mapped[List["ToolExecution"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )


class AgentDataSource(Base):
    __tablename__ = "agent_datasources"

    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), primary_key=True)
    datasource_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("datasources.id"), primary_key=True
    )


class ToolExecution(Base):
    __tablename__ = "tool_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    agent_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("agents.id"), nullable=True)
    conversation_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("conversations.id"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    parameters: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    agent: Mapped[Optional["Agent"]] = relationship(back_populates="tool_executions")


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    draft_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    source_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    current_commit_sha: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    release_commit_sha: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    current_release_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("page_releases.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    releases: Mapped[List["PageRelease"]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        foreign_keys="PageRelease.page_id",
    )
    build_runs: Mapped[List["PageBuildRun"]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        foreign_keys="PageBuildRun.page_id",
    )
    snapshots: Mapped[List["PageDraftSnapshot"]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        foreign_keys="PageDraftSnapshot.page_id",
    )
    compile_runs: Mapped[List["PageCompileRun"]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        foreign_keys="PageCompileRun.page_id",
    )
    current_release: Mapped[Optional["PageRelease"]] = relationship(
        foreign_keys=[current_release_id], post_update=True
    )


class PageRelease(Base):
    __tablename__ = "page_releases"
    __table_args__ = (
        UniqueConstraint("page_id", "version", name="uq_page_releases_page_id_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    page_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pages.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_uri: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    artifact_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    release_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    page: Mapped["Page"] = relationship(back_populates="releases", foreign_keys=[page_id])


class PageBuildRun(Base):
    __tablename__ = "page_build_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    page_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pages.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="running")
    phase: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    page: Mapped["Page"] = relationship(back_populates="build_runs", foreign_keys=[page_id])
    events: Mapped[List["PageBuildEvent"]] = relationship(
        back_populates="build_run",
        cascade="all, delete-orphan",
        foreign_keys="PageBuildEvent.build_run_id",
    )


class PageBuildEvent(Base):
    __tablename__ = "page_build_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    build_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("page_build_runs.id", ondelete="CASCADE"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="running")
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    build_run: Mapped["PageBuildRun"] = relationship(
        back_populates="events", foreign_keys=[build_run_id]
    )


class PageDraftSnapshot(Base):
    __tablename__ = "page_draft_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    page_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    snapshot_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    page: Mapped["Page"] = relationship(back_populates="snapshots", foreign_keys=[page_id])
    compile_runs: Mapped[List["PageCompileRun"]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        foreign_keys="PageCompileRun.snapshot_id",
    )


class PageCompileRun(Base):
    __tablename__ = "page_compile_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    page_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("page_draft_snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="running")
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    artifact_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    page: Mapped["Page"] = relationship(back_populates="compile_runs", foreign_keys=[page_id])
    snapshot: Mapped["PageDraftSnapshot"] = relationship(
        back_populates="compile_runs", foreign_keys=[snapshot_id]
    )


class Function(Base):
    __tablename__ = "functions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: f"fn-{uuid.uuid4().hex[:12]}",
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False, default="custom")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    draft_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    draft_dependencies: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    source_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    current_commit_sha: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    release_commit_sha: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    current_release_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("function_releases.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    releases: Mapped[List["FunctionRelease"]] = relationship(
        back_populates="function",
        cascade="all, delete-orphan",
        foreign_keys="FunctionRelease.function_id",
    )
    current_release: Mapped[Optional["FunctionRelease"]] = relationship(
        foreign_keys=[current_release_id], post_update=True
    )
    schedules: Mapped[List["Schedule"]] = relationship(
        back_populates="function", cascade="all, delete-orphan", foreign_keys="Schedule.function_id"
    )
    runs: Mapped[List["FunctionRun"]] = relationship(
        back_populates="function", cascade="all, delete-orphan", foreign_keys="FunctionRun.function_id"
    )
    build_runs: Mapped[List["FunctionBuildRun"]] = relationship(
        back_populates="function",
        cascade="all, delete-orphan",
        foreign_keys="FunctionBuildRun.function_id",
    )


class FunctionRelease(Base):
    __tablename__ = "function_releases"
    __table_args__ = (
        UniqueConstraint("function_id", "version", name="uq_function_releases_function_id_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    function_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("functions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    code_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    dependency_manifest: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    release_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    function: Mapped["Function"] = relationship(back_populates="releases", foreign_keys=[function_id])
    runs: Mapped[List["FunctionRun"]] = relationship(
        back_populates="function_release",
        cascade="all, delete-orphan",
        foreign_keys="FunctionRun.function_release_id",
    )


class FunctionRun(Base):
    __tablename__ = "function_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, unique=True)
    function_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("functions.id", ondelete="CASCADE"), nullable=False
    )
    function_release_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("function_releases.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="running")
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    input_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_class: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    runtime_context: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    function: Mapped["Function"] = relationship(back_populates="runs", foreign_keys=[function_id])
    function_release: Mapped[Optional["FunctionRelease"]] = relationship(
        back_populates="runs", foreign_keys=[function_release_id]
    )


class FunctionBuildRun(Base):
    __tablename__ = "function_build_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    function_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("functions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False, default="build")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="running")
    phase: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    function: Mapped["Function"] = relationship(back_populates="build_runs", foreign_keys=[function_id])
    events: Mapped[List["FunctionBuildEvent"]] = relationship(
        back_populates="build_run",
        cascade="all, delete-orphan",
        foreign_keys="FunctionBuildEvent.build_run_id",
    )


class FunctionBuildEvent(Base):
    __tablename__ = "function_build_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    build_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("function_build_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    phase: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="running")
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    build_run: Mapped["FunctionBuildRun"] = relationship(
        back_populates="events", foreign_keys=[build_run_id]
    )


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False, default="custom")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, default="function")
    target_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    schedule_type: Mapped[str] = mapped_column(String(20), nullable=False, default="cron")
    cron_expression: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    interval_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    datasource_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("datasources.id"), nullable=True)
    function_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("functions.id"), nullable=True)
    function_release_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("function_releases.id"), nullable=True
    )
    input_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    input_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_backoff_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    function: Mapped["Function"] = relationship(back_populates="schedules", foreign_keys=[function_id])
    datasource: Mapped[Optional["DataSource"]] = relationship(back_populates="schedules")
    function_release: Mapped[Optional["FunctionRelease"]] = relationship(
        foreign_keys=[function_release_id]
    )
    runs: Mapped[List["ScheduleRun"]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )


class ScheduleRun(Base):
    __tablename__ = "schedule_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    schedule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    target_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    runtime_run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    runtime_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    conversation_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("conversations.id"), nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    schedule: Mapped["Schedule"] = relationship(back_populates="runs")


class BuildSession(Base):
    __tablename__ = "build_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conversation_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True
    )
    scope_type: Mapped[str] = mapped_column(String(50), nullable=False, default="builder")
    scope_object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    scope_object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    conversation: Mapped[Optional["Conversation"]] = relationship(back_populates="build_sessions")


class ObjectAuditLog(Base):
    __tablename__ = "object_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    result: Mapped[str] = mapped_column(String(50), nullable=False)
    detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StatsRiskCandidate(Base):
    __tablename__ = "stats_risk_candidates"
    __table_args__ = (
        UniqueConstraint(
            "datasource_id",
            "database_name",
            "table_name",
            name="uq_stats_risk_candidate_object",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    datasource_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("datasources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    database_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    lifecycle_status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    latest_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    datasource: Mapped["DataSource"] = relationship()
    tags: Mapped[List["StatsRiskCandidateTag"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
    )
    runs: Mapped[List["StatsRiskAnalysisRun"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
    )


class StatsRiskCandidateTag(Base):
    __tablename__ = "stats_risk_candidate_tags"
    __table_args__ = (
        UniqueConstraint("candidate_id", "tag_key", name="uq_stats_risk_candidate_tag"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("stats_risk_candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tag_key: Mapped[str] = mapped_column(String(64), nullable=False)
    tag_label: Mapped[str] = mapped_column(String(120), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    facts: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    candidate: Mapped["StatsRiskCandidate"] = relationship(back_populates="tags")


class StatsRiskAnalysisRun(Base):
    __tablename__ = "stats_risk_analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    datasource_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("datasources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("stats_risk_candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    datasource: Mapped["DataSource"] = relationship()
    candidate: Mapped["StatsRiskCandidate"] = relationship(back_populates="runs")


class StatsRiskCollectionRun(Base):
    __tablename__ = "stats_risk_collection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    datasource_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("datasources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    datasource: Mapped["DataSource"] = relationship()


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    service_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    resource_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="user")
    pack_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    documents: Mapped[list["KnowledgeDocument"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    kb_id: Mapped[int] = mapped_column(Integer, ForeignKey("knowledge_bases.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_path: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    knowledge_base: Mapped["KnowledgeBase"] = relationship(back_populates="documents")


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
