"""add source and pack_id to knowledge_bases

Revision ID: 0002_knowledge_pack_fields
Revises: 0001_initial
Create Date: 2026-06-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_knowledge_pack_fields"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_bases",
        sa.Column("source", sa.String(50), nullable=True, server_default="user"),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column("pack_id", sa.String(100), nullable=True),
    )
    op.execute("UPDATE knowledge_bases SET source = 'user' WHERE source IS NULL")


def downgrade() -> None:
    op.drop_column("knowledge_bases", "pack_id")
    op.drop_column("knowledge_bases", "source")
