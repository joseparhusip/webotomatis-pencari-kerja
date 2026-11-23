"""user_profile.target_categories & target_keywords (target pencarian dari CV aktif)

Revision ID: c4e8a1d2f6b9
Revises: b7d4e2f1a9c3
Create Date: 2026-11-23 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4e8a1d2f6b9"
down_revision: str | None = "b7d4e2f1a9c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_profile", sa.Column("target_categories", postgresql.ARRAY(sa.String(length=20)),
                                            server_default="{}", nullable=False))
    op.add_column("user_profile", sa.Column("target_keywords", postgresql.ARRAY(sa.String(length=80)),
                                            server_default="{}", nullable=False))


def downgrade() -> None:
    op.drop_column("user_profile", "target_keywords")
    op.drop_column("user_profile", "target_categories")
