from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.models import TickLog
from trading.tick_ingest.api.schemas import TickEvent


class TickAuditStore:
    """Persists raw tick records to the tick_logs table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def log_tick(self, event: TickEvent, symbol: str) -> int:
        """
        Insert a TickLog row and return its auto-generated id.

        Uses session.flush() to obtain the DB-assigned id without waiting for a
        full commit so the id can be propagated downstream in the same request.
        """
        row = TickLog(
            instrument_token=event.instrument_token,
            symbol=symbol,
            instrument_type=event.instrument_type.value,
            last_price=Decimal(str(event.last_price)),
            volume=event.volume,
            received_at=event.timestamp,
        )
        async with self._sf() as session:
            async with session.begin():
                session.add(row)
                await session.flush()
                return row.id
