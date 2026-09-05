"""add datasource access level

Revision ID: 0005_datasource_access_level
Revises: 0004_conversation_context_snapshots
Create Date: 2026-09-04 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_datasource_access_level"
down_revision: str | None = "0004_conversation_context_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "datasources",
        sa.Column(
            "access_level",
            sa.String(length=50),
            nullable=False,
            server_default="user",
        ),
    )
    op.execute(
        "UPDATE datasources SET access_level = 'admin' "
        "WHERE LOWER(COALESCE(tenant_role, '')) IN ('sys', 'admin', 'root', 'superuser')"
    )


def downgrade() -> None:
    op.drop_column("datasources", "access_level")
