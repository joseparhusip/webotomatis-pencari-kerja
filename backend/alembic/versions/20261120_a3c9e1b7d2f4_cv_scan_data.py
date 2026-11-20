"""cv scan data & evidence from_cv

Revision ID: a3c9e1b7d2f4
Revises: 7888c3e44a61
Create Date: 2026-11-20 10:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3c9e1b7d2f4"
down_revision: str | None = "7888c3e44a61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cv_document", sa.Column("scan_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("letter_evidence", sa.Column("from_cv", sa.Boolean(), server_default="false", nullable=False))


def downgrade() -> None:
    op.drop_column("letter_evidence", "from_cv")
    op.drop_column("cv_document", "scan_data")
