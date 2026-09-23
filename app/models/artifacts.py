"""Immutable authoring revisions and executed checks, separate from Agent status."""

from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, String, Table, Text

from app.db.base import Base

skill_drafts = Table(
    "skill_drafts",
    Base.metadata,
    Column("id", String(32), primary_key=True),
    Column("actor_id", String(128), nullable=False),
    Column("revision", String(64), nullable=False),
    Column("content", JSON, nullable=False),
    Column("run_id", ForeignKey("agent_runs.id")),
    Column("updated_at", Float, nullable=False),
)

function_revisions = Table(
    "function_revisions",
    Base.metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "function_id", ForeignKey("functions.id", ondelete="CASCADE"), nullable=False, index=True
    ),
    Column("revision_hash", String(64), nullable=False),
    Column("code", Text, nullable=False),
    Column("dependencies", JSON, nullable=False),
    Column("run_id", ForeignKey("agent_runs.id")),
    Column("created_at", Float, nullable=False),
)

artifact_validations = Table(
    "artifact_validations",
    Base.metadata,
    Column("id", String(64), primary_key=True),
    Column("object_type", String(32), nullable=False),
    Column("object_id", Integer, nullable=False, index=True),
    Column("revision_id", String(64), nullable=False),
    Column("revision_hash", String(64), nullable=False),
    Column("run_id", ForeignKey("agent_runs.id")),
    Column("checks", JSON, nullable=False),
    Column("created_at", Float, nullable=False),
)

page_revisions = Table(
    "page_revisions",
    Base.metadata,
    Column("id", String(64), primary_key=True),
    Column("page_id", ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("revision_hash", String(64), nullable=False),
    Column("files", JSON, nullable=False),
    Column("bindings", JSON, nullable=False),
    Column("run_id", ForeignKey("agent_runs.id")),
    Column("created_at", Float, nullable=False),
)

page_compilations = Table(
    "page_compilations",
    Base.metadata,
    Column("validation_id", ForeignKey("artifact_validations.id"), primary_key=True),
    Column("html", Text, nullable=False),
    Column("artifact_hash", String(64), nullable=False),
)

page_owned_functions = Table(
    "page_owned_functions",
    Base.metadata,
    Column("page_id", ForeignKey("pages.id", ondelete="CASCADE"), primary_key=True),
    Column("function_id", ForeignKey("functions.id", ondelete="CASCADE"), primary_key=True),
)
