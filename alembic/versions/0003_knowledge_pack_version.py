"""add version and repo_subdirectory to knowledge_bases

Revision ID: 0003_knowledge_pack_version
Revises: 0002_knowledge_pack_fields
Create Date: 2026-06-15 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_knowledge_pack_version"
down_revision: Union[str, None] = "0002_knowledge_pack_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "knowledge_bases",
        sa.Column("version", sa.String(50), nullable=True),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column("repo_subdirectory", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_bases", "repo_subdirectory")
    op.drop_column("knowledge_bases", "version")
