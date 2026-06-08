from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.tick_ingest.api.schemas import TickEvent
from trading.tick_ingest.storage.models import TickLog


@dataclass
class AuditContext:
    """Base for all typed audit context objects. Subclass in the owning module."""


class AuditStore:
    """Persists tick, decision, and module audit records."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def log_tick(self, event: TickEvent, symbol: str) -> int:
        """Insert a TickLog row and return its auto-generated id."""
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

    async def log_decision(
        self,
        step: str,
        symbol: str,
        tick_log_id: int,
        context: AuditContext,
        algo_name: str | None = None,
        signal_id: UUID | None = None,
        session_id: str | None = None,
    ) -> None:
        from trading.core.models import DecisionLog

        async with self._sf() as session:
            async with session.begin():
                session.add(
                    DecisionLog(
                        tick_log_id=tick_log_id,
                        step=step,
                        algo_name=algo_name,
                        session_id=session_id,
                        symbol=symbol,
                        signal_id=signal_id,
                        context=json.dumps(asdict(context)),
                    )
                )

    async def log_audit(self, module: str, level: str, message: str) -> None:
        from trading.core.models import AuditLog

        async with self._sf() as session:
            async with session.begin():
                session.add(AuditLog(module=module, level=level, message=message))


class TickAuditStore:
    """Persists raw tick records only (lightweight — no decision/audit log)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def log_tick(self, event: TickEvent, symbol: str) -> int:
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
