"""add broker_tokens table with pgcrypto

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "broker_tokens",
        sa.Column("broker", sa.String(), nullable=False),
        sa.Column("token_enc", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("broker"),
    )


def downgrade() -> None:
    op.drop_table("broker_tokens")
