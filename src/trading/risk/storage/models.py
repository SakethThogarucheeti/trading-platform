from sqlalchemy.orm import DeclarativeBase

from trading.core.db_registry import shared_registry


class Base(DeclarativeBase):
    registry = shared_registry
    metadata = shared_registry.metadata

# Risk module tables: future home for risk_state snapshots.
