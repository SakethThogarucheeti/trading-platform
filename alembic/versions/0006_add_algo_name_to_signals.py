"""add algo_name to signals

Revision ID: 0006
Revises: bc159b538704
Create Date: 2026-05-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "bc159b538704"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("algo_name", sa.String(), nullable=True))
    op.create_index(op.f("ix_signals_algo_name"), "signals", ["algo_name"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_signals_algo_name"), table_name="signals")
    op.drop_column("signals", "algo_name")
