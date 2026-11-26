"""cv_review: hasil fitur Skor CV

Revision ID: d5f1b3c8e2a7
Revises: c4e8a1d2f6b9
Create Date: 2026-11-26 10:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5f1b3c8e2a7"
down_revision: str | None = "c4e8a1d2f6b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cv_review",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("file_type", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="upload"),
        sa.Column("method", sa.String(length=10), nullable=False, server_default="text"),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("scan_data", postgresql.JSONB(), nullable=True),
        sa.Column("report", postgresql.JSONB(), nullable=True),
        sa.Column("ai_review", postgresql.JSONB(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cv_review")
