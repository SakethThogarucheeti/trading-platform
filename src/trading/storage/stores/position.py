from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.models import Position
from trading.core.schemas import FillEvent, Side
from trading.execution.position_ledger import PositionLedger, PositionState


class AbstractPositionStore(ABC):
    @abstractmethod
    async def get_position(self, symbol: str, instrument_type: str) -> Position | None: ...

    @abstractmethod
    async def update_position(
        self, fill: FillEvent, side: Side, symbol: str, instrument_type: str
    ) -> None: ...


class PositionStore(AbstractPositionStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_position(self, symbol: str, instrument_type: str) -> Position | None:
        async with self._sf() as session:
            return await session.get(
                Position, {"symbol": symbol, "instrument_type": instrument_type}
            )

    async def update_position(
        self, fill: FillEvent, side: Side, symbol: str, instrument_type: str
    ) -> None:
        """
        Atomically update a position after a fill.

        Uses SELECT … FOR UPDATE so concurrent fills don't race.
        Delegates arithmetic to PositionLedger.apply_fill().
        """
        async with self._sf() as session:
            async with session.begin():
                result = await session.execute(
                    select(Position)
                    .where(
                        Position.symbol == symbol,
                        Position.instrument_type == instrument_type,
                    )
                    .with_for_update()
                )
                position = result.scalar_one_or_none()
                current = (
                    PositionState(net_qty=position.net_qty, avg_price=position.avg_price)
                    if position is not None
                    else None
                )
                new_state = PositionLedger.apply_fill(
                    current=current,
                    fill_qty=fill.filled_qty,
                    fill_price=Decimal(str(fill.avg_price)),
                    side=side,
                )
                if position is None:
                    session.add(
                        Position(
                            symbol=symbol,
                            instrument_type=instrument_type,
                            net_qty=new_state.net_qty,
                            avg_price=new_state.avg_price,
                            updated_at=datetime.now(UTC),
                        )
                    )
                else:
                    position.net_qty = new_state.net_qty
                    position.avg_price = new_state.avg_price
                    position.updated_at = datetime.now(UTC)
