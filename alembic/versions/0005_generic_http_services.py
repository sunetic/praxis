"""add generic HTTP service credentials and knowledge bindings

Revision ID: 0005_generic_http_services
Revises: 0004_conversation_context_snapshots
Create Date: 2026-09-04 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_generic_http_services"
down_revision: str | None = "0004_conversation_context_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("services", sa.Column("secrets", sa.Text(), nullable=True))
    op.create_table(
        "service_knowledge_bases",
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("knowledge_base_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("service_id", "knowledge_base_id"),
    )


def downgrade() -> None:
    op.drop_table("service_knowledge_bases")
    op.drop_column("services", "secrets")
