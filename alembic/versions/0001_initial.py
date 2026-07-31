"""initial

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-22 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "datasources",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("db_type", sa.String(50), nullable=False, server_default="mysql"),
        sa.Column("cluster_key", sa.String(255), nullable=False, server_default=""),
        sa.Column("tenant_role", sa.String(50), nullable=False, server_default="user"),
        sa.Column("user", sa.String(255), nullable=True),
        sa.Column("password", sa.String(512), nullable=True),
        sa.Column("database", sa.String(255), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "channels",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False, server_default="dingtalk"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("tools", sa.JSON(), nullable=True),
        sa.Column("skills", sa.JSON(), nullable=True),
        sa.Column("agent_type", sa.String(50), nullable=False, server_default="custom"),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("title", sa.String(500), nullable=False, server_default="New Conversation"),
        sa.Column("datasource_id", sa.Integer(), sa.ForeignKey("datasources.id"), nullable=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("active_skills", sa.JSON(), nullable=True),
        sa.Column("category", sa.String(20), nullable=False, server_default="primary"),
        sa.Column("scene_key", sa.String(100), nullable=True),
        sa.Column("read_only", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False
        ),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("agent_name", sa.String(100), nullable=True),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("content_parts", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "chat_events",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("phase", sa.String(50), nullable=True),
        sa.Column("turn_id", sa.String(64), nullable=True, index=True),
        sa.Column("turn_seq", sa.Integer(), nullable=True, index=True),
        sa.Column("part_seq", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(20), nullable=True),
        sa.Column("agent_name", sa.String(100), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "pending_actions",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False
        ),
        sa.Column("token", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("action_type", sa.String(50), nullable=False, server_default="execute_sql"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "agent_datasources",
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), primary_key=True),
        sa.Column("datasource_id", sa.Integer(), sa.ForeignKey("datasources.id"), primary_key=True),
    )

    op.create_table(
        "tool_executions",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column(
            "conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=True
        ),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "page_releases",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("page_id", sa.Integer(), nullable=False),  # FK added after pages table
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("artifact_uri", sa.String(500), nullable=True),
        sa.Column("artifact_payload", sa.JSON(), nullable=True),
        sa.Column("release_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("page_id", "version", name="uq_page_releases_page_id_version"),
    )

    op.create_table(
        "pages",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
        sa.Column("draft_payload", sa.JSON(), nullable=True),
        sa.Column("source_path", sa.String(500), nullable=True),
        sa.Column("current_commit_sha", sa.String(64), nullable=True),
        sa.Column("release_commit_sha", sa.String(64), nullable=True),
        sa.Column(
            "current_release_id",
            sa.Integer(),
            sa.ForeignKey("page_releases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # Add FK from page_releases.page_id → pages.id
    with op.batch_alter_table("page_releases") as batch_op:
        batch_op.create_foreign_key(
            "fk_page_releases_page_id",
            "pages",
            ["page_id"],
            ["id"],
            ondelete="CASCADE",
        )

    op.create_table(
        "page_build_runs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("run_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column(
            "page_id", sa.Integer(), sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column("phase", sa.String(50), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "page_build_events",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "build_run_id",
            sa.Integer(),
            sa.ForeignKey("page_build_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("phase", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "page_draft_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "page_id",
            sa.Integer(),
            sa.ForeignKey("pages.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("snapshot_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "page_compile_runs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("run_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column(
            "page_id",
            sa.Integer(),
            sa.ForeignKey("pages.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("page_draft_snapshots.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("artifact_payload", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "function_releases",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("function_id", sa.Integer(), nullable=False),  # FK added after functions
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("code_snapshot", sa.Text(), nullable=False),
        sa.Column("dependency_manifest", sa.JSON(), nullable=True),
        sa.Column("release_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "function_id", "version", name="uq_function_releases_function_id_version"
        ),
    )

    op.create_table(
        "functions",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(50), nullable=False, server_default="custom"),
        sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
        sa.Column("draft_code", sa.Text(), nullable=True),
        sa.Column("draft_dependencies", sa.JSON(), nullable=True),
        sa.Column("source_path", sa.String(500), nullable=True),
        sa.Column("current_commit_sha", sa.String(64), nullable=True),
        sa.Column("release_commit_sha", sa.String(64), nullable=True),
        sa.Column(
            "current_release_id",
            sa.Integer(),
            sa.ForeignKey("function_releases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # Add FK from function_releases.function_id → functions.id
    with op.batch_alter_table("function_releases") as batch_op:
        batch_op.create_foreign_key(
            "fk_function_releases_function_id",
            "functions",
            ["function_id"],
            ["id"],
            ondelete="CASCADE",
        )

    op.create_table(
        "function_runs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("run_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column(
            "function_id",
            sa.Integer(),
            sa.ForeignKey("functions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "function_release_id",
            sa.Integer(),
            sa.ForeignKey("function_releases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("error_class", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("runtime_context", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "function_build_runs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("run_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column(
            "function_id",
            sa.Integer(),
            sa.ForeignKey("functions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("action", sa.String(50), nullable=False, server_default="build"),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column("phase", sa.String(50), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "function_build_events",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "build_run_id",
            sa.Integer(),
            sa.ForeignKey("function_build_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("phase", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(50), nullable=False, server_default="custom"),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("target_type", sa.String(20), nullable=False, server_default="function"),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("schedule_type", sa.String(20), nullable=False, server_default="cron"),
        sa.Column("cron_expression", sa.String(255), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("datasource_id", sa.Integer(), sa.ForeignKey("datasources.id"), nullable=True),
        sa.Column("function_id", sa.Integer(), sa.ForeignKey("functions.id"), nullable=True),
        sa.Column(
            "function_release_id",
            sa.Integer(),
            sa.ForeignKey("function_releases.id"),
            nullable=True,
        ),
        sa.Column("input_payload", sa.JSON(), nullable=True),
        sa.Column("input_prompt", sa.Text(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_backoff_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "schedule_runs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "schedule_id",
            sa.Integer(),
            sa.ForeignKey("schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(64), nullable=False, index=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="queued"),
        sa.Column("trigger_type", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column("target_type", sa.String(20), nullable=True),
        sa.Column("runtime_run_id", sa.String(64), nullable=True),
        sa.Column("runtime_status", sa.String(50), nullable=True),
        sa.Column(
            "conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=True
        ),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("output_payload", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "build_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("scope_type", sa.String(50), nullable=False, server_default="builder"),
        sa.Column("scope_object_type", sa.String(50), nullable=False),
        sa.Column("scope_object_id", sa.String(64), nullable=False),
        sa.Column("ttl_seconds", sa.Integer(), nullable=False, server_default="1800"),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "object_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("object_type", sa.String(50), nullable=False),
        sa.Column("object_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("result", sa.String(50), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "stats_risk_candidates",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "datasource_id",
            sa.Integer(),
            sa.ForeignKey("datasources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("tenant_name", sa.String(255), nullable=True),
        sa.Column("database_name", sa.String(255), nullable=False),
        sa.Column("table_name", sa.String(255), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="low"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("lifecycle_status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("latest_summary", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "datasource_id", "database_name", "table_name", name="uq_stats_risk_candidate_object"
        ),
    )

    op.create_table(
        "stats_risk_candidate_tags",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("stats_risk_candidates.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("tag_key", sa.String(64), nullable=False),
        sa.Column("tag_label", sa.String(120), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="low"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("facts", sa.JSON(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("candidate_id", "tag_key", name="uq_stats_risk_candidate_tag"),
    )

    op.create_table(
        "stats_risk_analysis_runs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("run_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column(
            "datasource_id",
            sa.Integer(),
            sa.ForeignKey("datasources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("stats_risk_candidates.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("trigger_type", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "stats_risk_collection_runs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("run_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column(
            "datasource_id",
            sa.Integer(),
            sa.ForeignKey("datasources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("trigger_type", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("service_type", sa.String(50), nullable=False),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("resource_ref", sa.String(255), nullable=True, index=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "kb_id", sa.Integer(), sa.ForeignKey("knowledge_bases.id"), nullable=False, index=True
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_path", sa.String(512), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "platform_settings",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("platform_settings")
    op.drop_table("knowledge_documents")
    op.drop_table("knowledge_bases")
    op.drop_table("services")
    op.drop_table("stats_risk_collection_runs")
    op.drop_table("stats_risk_analysis_runs")
    op.drop_table("stats_risk_candidate_tags")
    op.drop_table("stats_risk_candidates")
    op.drop_table("object_audit_logs")
    op.drop_table("build_sessions")
    op.drop_table("schedule_runs")
    op.drop_table("schedules")
    op.drop_table("function_build_events")
    op.drop_table("function_build_runs")
    op.drop_table("function_runs")
    with op.batch_alter_table("function_releases") as batch_op:
        batch_op.drop_constraint("fk_function_releases_function_id", type_="foreignkey")
    op.drop_table("functions")
    op.drop_table("function_releases")
    op.drop_table("page_compile_runs")
    op.drop_table("page_draft_snapshots")
    op.drop_table("page_build_events")
    op.drop_table("page_build_runs")
    with op.batch_alter_table("page_releases") as batch_op:
        batch_op.drop_constraint("fk_page_releases_page_id", type_="foreignkey")
    op.drop_table("pages")
    op.drop_table("page_releases")
    op.drop_table("tool_executions")
    op.drop_table("agent_datasources")
    op.drop_table("pending_actions")
    op.drop_table("chat_events")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("agents")
    op.drop_table("channels")
    op.drop_table("datasources")
