"""add client_tag to orders (trading-platform#31)

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-14

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("client_tag", sa.String(length=20), nullable=True))
    op.create_index("ix_orders_client_tag", "orders", ["client_tag"])


def downgrade() -> None:
    op.drop_index("ix_orders_client_tag", table_name="orders")
    op.drop_column("orders", "client_tag")
