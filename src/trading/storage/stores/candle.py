from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from quantindicators.types import CandleRow

from trading.core.models import Candle


def _candle_to_dict(c: Candle) -> CandleRow:
    return CandleRow(
        symbol=c.symbol,
        interval=c.interval,
        ts=c.ts,
        open=float(c.open),
        high=float(c.high),
        low=float(c.low),
        close=float(c.close),
        volume=c.volume,
    )


class AbstractCandleDataStore(ABC):
    @abstractmethod
    async def save_candles(self, rows: list[CandleRow]) -> None: ...

    @abstractmethod
    async def get_candles(self, symbol: str, interval: str, limit: int) -> list[CandleRow]: ...

    @abstractmethod
    async def get_candles_since(self, symbol: str, interval: str, since: datetime) -> list[CandleRow]: ...


class CandleDataStore(AbstractCandleDataStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def save_candles(self, rows: list[CandleRow]) -> None:
        if not rows:
            return
        stmt = (
            insert(Candle)
            .values(
                [
                    {
                        "symbol": r["symbol"],
                        "interval": r["interval"],
                        "ts": r["ts"],
                        "open": Decimal(str(r["open"])),
                        "high": Decimal(str(r["high"])),
                        "low": Decimal(str(r["low"])),
                        "close": Decimal(str(r["close"])),
                        "volume": int(r["volume"]),
                    }
                    for r in rows
                ]
            )
            .on_conflict_do_nothing(constraint="uq_candle_symbol_interval_ts")
        )
        async with self._sf() as session:
            async with session.begin():
                await session.execute(stmt)

    async def get_candles(self, symbol: str, interval: str, limit: int) -> list[CandleRow]:
        async with self._sf() as session:
            result = await session.execute(
                select(Candle)
                .where(Candle.symbol == symbol, Candle.interval == interval)
                .order_by(Candle.ts.desc())
                .limit(limit)
            )
            rows = list(reversed(result.scalars().all()))
        return [_candle_to_dict(c) for c in rows]

    async def get_candles_since(self, symbol: str, interval: str, since: datetime) -> list[CandleRow]:
        async with self._sf() as session:
            result = await session.execute(
                select(Candle)
                .where(
                    Candle.symbol == symbol,
                    Candle.interval == interval,
                    Candle.ts >= since,
                )
                .order_by(Candle.ts.asc())
            )
            return [_candle_to_dict(c) for c in result.scalars().all()]
