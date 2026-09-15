"""add failed_dispatches table (trading-platform#94)

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-16

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "failed_dispatches",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("site", sa.String, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("error", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("failed_dispatches")
