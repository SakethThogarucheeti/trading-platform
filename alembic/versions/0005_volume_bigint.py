"""volume columns to bigint

Revision ID: 0005
Revises: bc159b538704
Create Date: 2026-05-15

Intraday cumulative volume for liquid NSE instruments regularly exceeds
2^31 (~2.1B), overflowing PostgreSQL int4. Switch both candles.volume
and tick_logs.volume to int8 (bigint).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "bc159b538704"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("candles", "volume", type_=sa.BigInteger(), existing_type=sa.Integer())
    op.alter_column("tick_logs", "volume", type_=sa.BigInteger(), existing_type=sa.Integer())


def downgrade() -> None:
    op.alter_column("tick_logs", "volume", type_=sa.Integer(), existing_type=sa.BigInteger())
    op.alter_column("candles", "volume", type_=sa.Integer(), existing_type=sa.BigInteger())
