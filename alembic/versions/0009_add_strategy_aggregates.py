"""add strategy_aggregates table (Postgres-only replacement for Redis PnL cache)

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_aggregates",
        sa.Column("metric", sa.String(), nullable=False),
        sa.Column("for_date", sa.Date(), nullable=False),
        sa.Column("algo_name", sa.String(), nullable=False, server_default="ALL"),
        sa.Column("symbol", sa.String(), nullable=False, server_default="ALL"),
        sa.Column("value", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("metric", "for_date", "algo_name", "symbol"),
    )


def downgrade() -> None:
    op.drop_table("strategy_aggregates")
