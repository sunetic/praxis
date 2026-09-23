import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import EncryptedJSON, EncryptedString
from app.db.base import Base
from app.models import agent_runs as native_agent_tables  # noqa: F401

service_knowledge_bases = Table(
    "service_knowledge_bases",
    Base.metadata,
    Column(
        "service_id",
        Integer,
        ForeignKey("services.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "knowledge_base_id",
        Integer,
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class DataSource(Base):
    __tablename__ = "datasources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    db_type: Mapped[str] = mapped_column(String(50), default="mysql")
    cluster_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    access_level: Mapped[str] = mapped_column(String(50), nullable=False, default="user")
    tenant_role: Mapped[str] = mapped_column(String(50), nullable=False, default="user")

    user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    database: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attributes: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(String(50), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    agents: Mapped[list["Agent"]] = relationship(
        secondary="agent_datasources", back_populates="datasources"
    )
    schedules: Mapped[list["Schedule"]] = relationship(back_populates="datasource")


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="dingtalk")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    tools: Mapped[str | None] = mapped_column(JSON, nullable=True)
    skills: Mapped[str | None] = mapped_column(JSON, nullable=True)
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False, default="custom")
    status: Mapped[str] = mapped_column(String(50), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    datasources: Mapped[list["DataSource"]] = relationship(
        secondary="agent_datasources", back_populates="agents"
    )

    @property
    def datasource_ids(self) -> list[int]:
        return [item.id for item in self.datasources]


class AgentDataSource(Base):
    __tablename__ = "agent_datasources"

    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), primary_key=True)
    datasource_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("datasources.id"), primary_key=True
    )


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    draft_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    current_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    release_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_release_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "page_releases.id",
            name="fk_pages_current_release_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    releases: Mapped[list["PageRelease"]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        foreign_keys="PageRelease.page_id",
    )
    build_runs: Mapped[list["PageBuildRun"]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        foreign_keys="PageBuildRun.page_id",
    )
    snapshots: Mapped[list["PageDraftSnapshot"]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        foreign_keys="PageDraftSnapshot.page_id",
    )
    compile_runs: Mapped[list["PageCompileRun"]] = relationship(
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
    artifact_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    artifact_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    release_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    phase: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    page: Mapped["Page"] = relationship(back_populates="build_runs", foreign_keys=[page_id])
    events: Mapped[list["PageBuildEvent"]] = relationship(
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
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    page: Mapped["Page"] = relationship(back_populates="snapshots", foreign_keys=[page_id])
    compile_runs: Mapped[list["PageCompileRun"]] = relationship(
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
        Integer,
        ForeignKey("page_draft_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="running")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False, default="custom")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    draft_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_dependencies: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    current_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    release_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_release_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "function_releases.id",
            name="fk_functions_current_release_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    releases: Mapped[list["FunctionRelease"]] = relationship(
        back_populates="function",
        cascade="all, delete-orphan",
        foreign_keys="FunctionRelease.function_id",
    )
    current_release: Mapped[Optional["FunctionRelease"]] = relationship(
        foreign_keys=[current_release_id], post_update=True
    )
    schedules: Mapped[list["Schedule"]] = relationship(
        back_populates="function", cascade="all, delete-orphan", foreign_keys="Schedule.function_id"
    )
    runs: Mapped[list["FunctionRun"]] = relationship(
        back_populates="function",
        cascade="all, delete-orphan",
        foreign_keys="FunctionRun.function_id",
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
    dependency_manifest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    release_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    function: Mapped["Function"] = relationship(
        back_populates="releases", foreign_keys=[function_id]
    )
    runs: Mapped[list["FunctionRun"]] = relationship(
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
    function_release_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("function_releases.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="running")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_payload: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSON, nullable=True
    )
    error_class: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    runtime_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    function: Mapped["Function"] = relationship(back_populates="runs", foreign_keys=[function_id])
    function_release: Mapped[Optional["FunctionRelease"]] = relationship(
        back_populates="runs", foreign_keys=[function_release_id]
    )


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False, default="custom")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, default="function")
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_type: Mapped[str] = mapped_column(String(20), nullable=False, default="cron")
    cron_expression: Mapped[str | None] = mapped_column(String(255), nullable=True)
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    datasource_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("datasources.id"), nullable=True
    )
    function_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("functions.id"), nullable=True
    )
    function_release_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("function_releases.id"), nullable=True
    )
    input_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    input_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_backoff_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    function: Mapped["Function"] = relationship(
        back_populates="schedules", foreign_keys=[function_id]
    )
    datasource: Mapped[Optional["DataSource"]] = relationship(back_populates="schedules")
    function_release: Mapped[Optional["FunctionRelease"]] = relationship(
        foreign_keys=[function_release_id]
    )
    runs: Mapped[list["ScheduleRun"]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )


class ScheduleRun(Base):
    __tablename__ = "schedule_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    schedule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    runtime_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runtime_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("agent_conversations.id"), nullable=True
    )
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    schedule: Mapped["Schedule"] = relationship(back_populates="runs")


class ObjectAuditLog(Base):
    __tablename__ = "object_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    result: Mapped[str] = mapped_column(String(50), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    service_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    secrets: Mapped[dict | None] = mapped_column(EncryptedJSON, nullable=True)
    resource_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    knowledge_bases: Mapped[list["KnowledgeBase"]] = relationship(
        secondary=service_knowledge_bases,
        back_populates="services",
    )

    @property
    def has_credentials(self) -> bool:
        return bool(self.secrets)

    @property
    def knowledge_base_ids(self) -> list[int]:
        return [item.id for item in self.knowledge_bases]


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True, default="user")
    pack_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    repo_subdirectory: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    documents: Mapped[list["KnowledgeDocument"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )
    services: Mapped[list["Service"]] = relationship(
        secondary=service_knowledge_bases,
        back_populates="knowledge_bases",
    )


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    kb_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("knowledge_bases.id"), nullable=False, index=True
    )
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
    value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
