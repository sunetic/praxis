"""add datasource access level

Revision ID: 0006_datasource_access_level
Revises: 0005_generic_http_services
Create Date: 2026-09-05 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_datasource_access_level"
down_revision: str | None = "0005_generic_http_services"
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
