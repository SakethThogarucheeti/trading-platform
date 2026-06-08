from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from trading.core.clock import Clock
from trading.candles.storage.models import Candle
from trading.core.models import DecisionLog
from trading.execution.storage.models import Position
from trading.monitoring.storage.models import Heartbeat
from trading.tick_ingest.storage.models import TickLog

from ._helpers import session_filter


def create_market_router(
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    heartbeat_stale_secs: int,
) -> APIRouter:
    router = APIRouter()

    def _today_start() -> datetime:
        today = clock.today()
        return datetime(today.year, today.month, today.day, tzinfo=clock.tz).astimezone(UTC)

    @router.get("/api/ping")
    async def ping() -> JSONResponse:
        return JSONResponse(content={"ok": True})

    @router.get("/api/health")
    async def get_health() -> JSONResponse:
        async with session_factory() as session:
            result = await session.execute(select(Heartbeat).order_by(Heartbeat.module))
            heartbeats = result.scalars().all()

        now = clock.now()
        rows: list[dict[str, object]] = []
        for hb in heartbeats:
            last_seen = hb.last_seen
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=UTC)
            stale = (now - last_seen).total_seconds() > heartbeat_stale_secs
            rows.append(
                {
                    "module": hb.module,
                    "last_seen": last_seen.isoformat(),
                    "stale": stale,
                }
            )
        return JSONResponse(content=rows)

    @router.get("/api/positions")
    async def get_positions() -> JSONResponse:
        async with session_factory() as session:
            result = await session.execute(
                select(Position)
                .where(Position.updated_at >= _today_start())
                .order_by(Position.symbol)
            )
            positions = result.scalars().all()

        return JSONResponse(
            content=[
                {
                    "symbol": p.symbol,
                    "instrument_type": p.instrument_type,
                    "net_qty": p.net_qty,
                    "avg_price": float(p.avg_price),
                    "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                }
                for p in positions
            ]
        )

    @router.get("/api/signals")
    async def get_signals(session_id: str = "", algo_name: str = "") -> JSONResponse:
        async with session_factory() as session:
            conditions: list[ColumnElement[bool]] = [
                DecisionLog.step.in_(["SIGNAL_GENERATED", "SIGNAL_ACCEPTED", "SIGNAL_REJECTED"]),
                DecisionLog.created_at >= _today_start(),
                session_filter(DecisionLog, session_id),
            ]
            if algo_name:
                conditions.append(DecisionLog.algo_name == algo_name)
            stmt = (
                select(DecisionLog)
                .where(*conditions)
                .order_by(DecisionLog.created_at.desc())
                .limit(50)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

        return JSONResponse(
            content=[
                {
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "symbol": r.symbol,
                    "algo_name": r.algo_name or "—",
                    "step": r.step,
                    "context": r.context,
                }
                for r in rows
            ]
        )

    @router.get("/api/candles")
    async def get_candles_endpoint(
        symbol: str = "INFY",
        interval: str = "15min",
        limit: int = 100,
    ) -> JSONResponse:
        async with session_factory() as session:
            result = await session.execute(
                select(Candle)
                .where(
                    Candle.symbol == symbol,
                    Candle.interval == interval,
                    Candle.ts >= _today_start(),
                )
                .order_by(Candle.ts.desc())
                .limit(limit)
            )
            rows = list(reversed(result.scalars().all()))

        points = [
            {
                "ts": c.ts.isoformat(),
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": c.volume,
            }
            for c in rows
        ]
        return JSONResponse(content=points)

    @router.get("/api/ticks")
    async def get_ticks(symbol: str = "INFY", limit: int = 500) -> JSONResponse:
        async with session_factory() as session:
            result = await session.execute(
                select(TickLog)
                .where(
                    TickLog.symbol == symbol,
                    TickLog.received_at >= _today_start(),
                )
                .order_by(TickLog.received_at.desc())
                .limit(limit)
            )
            ticks = list(reversed(result.scalars().all()))

        points = [{"ts": t.received_at.isoformat(), "price": float(t.last_price)} for t in ticks]
        return JSONResponse(content=points)

    return router
