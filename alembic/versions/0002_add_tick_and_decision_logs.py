"""Add tick_logs and decision_logs tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-24 00:00:00.000000

NOTE: tick_logs and decision_logs were folded into 0001 during development.
This migration is kept as a no-op to preserve the revision chain.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tables already created in 0001; indexes added here for any DB that ran
    # 0001 without them. Use IF NOT EXISTS equivalent via try/except per index.
    connection = op.get_bind()
    for index_sql in [
        "CREATE INDEX IF NOT EXISTS ix_tick_logs_instrument_token ON tick_logs (instrument_token)",
        "CREATE INDEX IF NOT EXISTS ix_tick_logs_symbol ON tick_logs (symbol)",
        "CREATE INDEX IF NOT EXISTS ix_tick_logs_received_at ON tick_logs (received_at)",
        "CREATE INDEX IF NOT EXISTS ix_decision_logs_tick_log_id ON decision_logs (tick_log_id)",
        "CREATE INDEX IF NOT EXISTS ix_decision_logs_step ON decision_logs (step)",
        "CREATE INDEX IF NOT EXISTS ix_decision_logs_algo_name ON decision_logs (algo_name)",
        "CREATE INDEX IF NOT EXISTS ix_decision_logs_session_id ON decision_logs (session_id)",
        "CREATE INDEX IF NOT EXISTS ix_decision_logs_symbol ON decision_logs (symbol)",
        "CREATE INDEX IF NOT EXISTS ix_decision_logs_created_at ON decision_logs (created_at)",
    ]:
        connection.execute(text(index_sql))


def downgrade() -> None:
    pass
