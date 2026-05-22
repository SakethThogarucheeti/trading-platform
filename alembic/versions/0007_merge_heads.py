"""merge heads

Revision ID: 0007
Revises: 0005, 0006
Create Date: 2026-05-22

"""

from alembic import op
from typing import Sequence

revision: str = "0007"
down_revision: Sequence[str] = ("0005", "0006")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
