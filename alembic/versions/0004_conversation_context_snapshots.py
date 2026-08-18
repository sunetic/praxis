"""add conversation context snapshots

Revision ID: 0004_conversation_context_snapshots
Revises: 0003_knowledge_pack_version
Create Date: 2026-08-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_conversation_context_snapshots"
down_revision: str | None = "0003_knowledge_pack_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_context_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("through_message_id", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary_token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("prompt_version", sa.String(length=50), nullable=False, server_default="v1"),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "revision", name="uq_context_snapshot_revision"),
        sa.UniqueConstraint(
            "conversation_id",
            "through_message_id",
            name="uq_context_snapshot_boundary",
        ),
    )
    op.create_index(
        op.f("ix_conversation_context_snapshots_id"),
        "conversation_context_snapshots",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_context_snapshots_conversation_id"),
        "conversation_context_snapshots",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_context_snapshots_through_message_id"),
        "conversation_context_snapshots",
        ["through_message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_conversation_context_snapshots_through_message_id"),
        table_name="conversation_context_snapshots",
    )
    op.drop_index(
        op.f("ix_conversation_context_snapshots_conversation_id"),
        table_name="conversation_context_snapshots",
    )
    op.drop_index(
        op.f("ix_conversation_context_snapshots_id"),
        table_name="conversation_context_snapshots",
    )
    op.drop_table("conversation_context_snapshots")
