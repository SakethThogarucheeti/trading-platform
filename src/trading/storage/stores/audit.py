from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.models import AuditLog, DecisionLog, TickLog
from trading.core.schemas import TickEvent


@dataclass
class AuditContext:
    """Base for all typed audit context objects. Subclass in the owning registry module."""


class AbstractAuditStore(ABC):
    @abstractmethod
    async def log_tick(self, event: TickEvent, symbol: str) -> int: ...

    @abstractmethod
    async def log_decision(
        self,
        step: str,
        symbol: str,
        tick_log_id: int,
        context: AuditContext,
        algo_name: str | None = None,
        signal_id: UUID | None = None,
        session_id: str | None = None,
    ) -> None: ...

    @abstractmethod
    async def log_audit(self, module: str, level: str, message: str) -> None: ...


class AuditStore(AbstractAuditStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def log_tick(self, event: TickEvent, symbol: str) -> int:
        """
        Insert a TickLog row and return its auto-generated id.

        Uses session.flush() to obtain the DB-assigned id without waiting for a
        full commit. This id is propagated through every downstream event so all
        DecisionLog rows can be traced back to the originating tick.
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
        async with self._sf() as session:
            async with session.begin():
                session.add(AuditLog(module=module, level=level, message=message))
