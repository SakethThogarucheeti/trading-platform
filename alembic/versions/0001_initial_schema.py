"""Initial schema — all tables

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("token", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(), nullable=False, index=True),
        sa.Column("exchange", sa.String(), nullable=False),
        sa.Column("instrument_type", sa.String(), nullable=False),
        sa.Column("underlying", sa.String(), nullable=True),
        sa.Column("expiry", sa.Date(), nullable=True),
        sa.Column("strike", sa.Numeric(12, 4), nullable=True),
        sa.Column("option_type", sa.String(2), nullable=True),
        sa.Column("lot_size", sa.Integer(), nullable=True),
    )

    op.create_table(
        "signals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("strategy_id", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("instrument_type", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("signal_type", sa.String(), nullable=False),
        sa.Column("stop_distance", sa.Numeric(12, 4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "kite_order_id",
            sa.String(),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column(
            "signal_id",
            sa.Uuid(),
            sa.ForeignKey("signals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("avg_price", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "positions",
        sa.Column("symbol", sa.String(), primary_key=True),
        sa.Column("instrument_type", sa.String(), primary_key=True),
        sa.Column("net_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_price", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "heartbeats",
        sa.Column("module", sa.String(), primary_key=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("module", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "tick_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("instrument_token", sa.Integer(), nullable=False, index=True),
        sa.Column("symbol", sa.String(), nullable=False, index=True),
        sa.Column("instrument_type", sa.String(), nullable=False),
        sa.Column("last_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            index=True,
        ),
    )

    op.create_table(
        "decision_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tick_log_id",
            sa.Integer(),
            sa.ForeignKey("tick_logs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("step", sa.String(), nullable=False, index=True),
        sa.Column("algo_name", sa.String(), nullable=True, index=True),
        sa.Column("session_id", sa.String(), nullable=True, index=True),
        sa.Column("symbol", sa.String(), nullable=False, index=True),
        sa.Column("signal_id", sa.Uuid(), nullable=True),
        sa.Column("context", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("decision_logs")
    op.drop_table("tick_logs")
    op.drop_table("audit_logs")
    op.drop_table("heartbeats")
    op.drop_table("positions")
    op.drop_table("orders")
    op.drop_table("signals")
    op.drop_table("instruments")
