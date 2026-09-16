"""widen positions PK with algo_name, denormalize algo_name onto orders (trading-platform#83)

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-16

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # trading-platform#83: there is no principled way to attribute today's
    # existing blended (symbol, instrument_type) position rows to any one
    # algo retroactively -- per explicit human sign-off, reset the table to
    # empty on deploy rather than guessing an attribution. Every open
    # position is EOD-squared-off daily by this system anyway, so a deploy
    # right after a square-off (or before market open) loses nothing real.
    op.execute("DELETE FROM positions")

    op.add_column("positions", sa.Column("algo_name", sa.String(), nullable=False))
    op.drop_constraint("positions_pkey", "positions", type_="primary")
    op.create_primary_key(
        "positions_pkey", "positions", ["symbol", "instrument_type", "algo_name"]
    )

    op.add_column("orders", sa.Column("algo_name", sa.String(), nullable=True))
    op.create_index("ix_orders_algo_name", "orders", ["algo_name"])


def downgrade() -> None:
    op.drop_index("ix_orders_algo_name", table_name="orders")
    op.drop_column("orders", "algo_name")

    op.drop_constraint("positions_pkey", "positions", type_="primary")
    op.create_primary_key("positions_pkey", "positions", ["symbol", "instrument_type"])
    op.drop_column("positions", "algo_name")
