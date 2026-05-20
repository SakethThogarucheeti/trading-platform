"""Add algo_configs and algo_state tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-24 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "algo_configs",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("strategy_id", sa.String(), nullable=False),
        sa.Column("warmup_candles", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("candle_intervals", sa.String(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("params", sa.String(), nullable=False, server_default="{}"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name"),
    )

    op.create_table(
        "algo_state",
        sa.Column("name", sa.String(), sa.ForeignKey("algo_configs.name"), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="{}"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("algo_state")
    op.drop_table("algo_configs")
