"""job.region_code (kode wilayah Kemendagri untuk filter lokasi)

Revision ID: b7d4e2f1a9c3
Revises: a3c9e1b7d2f4
Create Date: 2026-11-23 15:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d4e2f1a9c3"
down_revision: str | None = "a3c9e1b7d2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job", sa.Column("region_code", sa.String(length=8), nullable=True))
    op.create_index("ix_job_region_code", "job", ["region_code"])


def downgrade() -> None:
    op.drop_index("ix_job_region_code", table_name="job")
    op.drop_column("job", "region_code")
