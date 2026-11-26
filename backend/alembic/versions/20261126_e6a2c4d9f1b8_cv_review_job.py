"""cv_review: kecocokan dengan satu lowongan

Revision ID: e6a2c4d9f1b8
Revises: d5f1b3c8e2a7
Create Date: 2026-11-26 14:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6a2c4d9f1b8"
down_revision: str | None = "d5f1b3c8e2a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cv_review", sa.Column("job_id", sa.Integer(), sa.ForeignKey("job.id", ondelete="SET NULL"), nullable=True))
    op.add_column("cv_review", sa.Column("job_title", sa.String(length=255), nullable=True))
    op.add_column("cv_review", sa.Column("job_text", sa.Text(), nullable=True))
    op.add_column("cv_review", sa.Column("job_match", postgresql.JSONB(), nullable=True))
    op.add_column("cv_review", sa.Column("job_score", sa.Integer(), nullable=True))


def downgrade() -> None:
    for col in ("job_score", "job_match", "job_text", "job_title", "job_id"):
        op.drop_column("cv_review", col)
