"""add candles table

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("interval", sa.String(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(14, 4), nullable=False),
        sa.Column("high", sa.Numeric(14, 4), nullable=False),
        sa.Column("low", sa.Numeric(14, 4), nullable=False),
        sa.Column("close", sa.Numeric(14, 4), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "interval", "ts", name="uq_candle_symbol_interval_ts"),
    )
    op.create_index("ix_candle_symbol_interval_ts", "candles", ["symbol", "interval", "ts"])
    op.create_index(op.f("ix_candles_symbol"), "candles", ["symbol"])
    op.create_index(op.f("ix_candles_ts"), "candles", ["ts"])


def downgrade() -> None:
    op.drop_index(op.f("ix_candles_ts"), table_name="candles")
    op.drop_index(op.f("ix_candles_symbol"), table_name="candles")
    op.drop_index("ix_candle_symbol_interval_ts", table_name="candles")
    op.drop_table("candles")
