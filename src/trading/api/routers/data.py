from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.candles.historical_data_service import HistoricalDataService
from trading.core.clock import Clock
from trading.core.models import DecisionLog, Instrument


def create_data_router(
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    candle_intervals: list[str],
    historical_data_service: HistoricalDataService | None,
) -> APIRouter:
    router = APIRouter()

    def _today_start() -> datetime:
        today = clock.today()
        return datetime(today.year, today.month, today.day, tzinfo=clock.tz).astimezone(UTC)

    @router.get("/api/sessions")
    async def get_sessions() -> JSONResponse:
        async with session_factory() as session:
            result = await session.execute(
                select(DecisionLog.session_id).distinct().order_by(DecisionLog.session_id)
            )
            rows = result.fetchall()

        sessions = [r[0] for r in rows]
        return JSONResponse(content=sessions)

    @router.get("/api/settings")
    async def get_settings_endpoint() -> JSONResponse:
        return JSONResponse(content={"candle_intervals": candle_intervals})

    @router.get("/api/instruments")
    async def get_instruments() -> JSONResponse:
        async with session_factory() as session:
            result = await session.execute(
                select(Instrument).order_by(Instrument.symbol)
            )
            instruments = result.scalars().all()
        return JSONResponse(content=[
            {
                "token": i.token,
                "symbol": i.symbol,
                "exchange": i.exchange,
                "instrument_type": i.instrument_type,
                "underlying": i.underlying,
                "expiry": i.expiry.isoformat() if i.expiry else None,
                "strike": float(i.strike) if i.strike is not None else None,
                "option_type": i.option_type,
                "lot_size": i.lot_size,
            }
            for i in instruments
        ])

    @router.get("/api/trades")
    async def get_trades(
        start: str = "",
        end: str = "",
        algo_name: str = "",
    ) -> JSONResponse:
        from trading.reports.trades import fetch_filled_trades

        try:
            start_dt = datetime.fromisoformat(start) if start else _today_start()
            end_dt = datetime.fromisoformat(end) if end else clock.now()
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=UTC)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=UTC)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid datetime: {exc}") from exc

        trades = await fetch_filled_trades(
            session_factory, start=start_dt, end=end_dt, algo_name=algo_name
        )
        return JSONResponse(content=[
            {
                "order_id": t.order_id,
                "kite_order_id": t.kite_order_id,
                "algo_name": t.algo_name,
                "strategy_id": t.strategy_id,
                "symbol": t.symbol,
                "instrument_type": t.instrument_type,
                "side": t.side,
                "signal_type": t.signal_type,
                "qty": t.qty,
                "avg_price": round(t.avg_price, 4),
                "gross": round(t.gross, 2),
                "cost": round(t.cost, 2),
                "net": round(t.net, 2),
                "filled_at": t.filled_at.isoformat() if t.filled_at else None,
            }
            for t in trades
        ])

    @router.get("/api/candles/history")
    async def get_candles_history(
        symbol: str,
        interval: str,
        start: str,
        end: str,
    ) -> JSONResponse:
        if historical_data_service is None:
            raise HTTPException(status_code=503, detail="Historical data service not available")
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=UTC)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=UTC)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid datetime: {exc}") from exc

        result = await historical_data_service.fetch(symbol, interval, start_dt, end_dt)
        return JSONResponse(content=result.df.to_dicts())

    return router
