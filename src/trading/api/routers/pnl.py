from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.clock import Clock
from trading.storage.cache import CacherFactory


def create_pnl_router(
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    cacher_factory: CacherFactory | None,
) -> APIRouter:
    router = APIRouter()

    def _today_start() -> datetime:
        today = clock.today()
        return datetime(today.year, today.month, today.day, tzinfo=clock.tz).astimezone(UTC)

    @router.get("/api/pnl")
    async def get_pnl(session_id: str = "", algo_name: str = "") -> JSONResponse:
        async def _produce() -> str:
            from trading.reports.fetch import fetch_nifty_benchmark
            from trading.reports.trades import fetch_filled_trades

            today = _today_start()
            trades = await fetch_filled_trades(
                session_factory, start=today, end=clock.now(), algo_name=algo_name
            )

            async with session_factory() as session:
                nifty = await fetch_nifty_benchmark(session, today, clock.now())

            cum_gross = 0.0
            cum_net = 0.0
            points: list[dict[str, object]] = []
            for t in trades:
                cum_gross += t.gross
                cum_net += t.net
                points.append(
                    {
                        "ts": t.filled_at.isoformat() if t.filled_at else "",
                        "cumulative_gross": round(cum_gross, 2),
                        "cumulative_net": round(cum_net, 2),
                        "side": t.side,
                        "qty": t.qty,
                        "price": round(t.avg_price, 2),
                        "cost": round(t.cost, 2),
                        "symbol": t.symbol,
                        "signal_type": t.signal_type,
                    }
                )

            summary: dict[str, object] = {
                "gross": round(cum_gross, 2),
                "costs": round(cum_gross - cum_net, 2),
                "net": round(cum_net, 2),
                "nifty_pct": round(nifty.pct_return, 2) if nifty else None,
                "nifty_open": round(nifty.open, 2) if nifty else None,
                "nifty_close": round(nifty.close, 2) if nifty else None,
            }
            return json.dumps({"points": points, "summary": summary})

        if cacher_factory is not None:
            today_iso = clock.now().date().isoformat()
            body = await cacher_factory.api().get_or_set_response(  # type: ignore[reportUnknownMemberType]
                key_args=("pnl", today_iso, session_id, algo_name),
                producer=_produce,
                ttl=30,
            )
            return JSONResponse(content=json.loads(body))
        return JSONResponse(content=json.loads(await _produce()))

    @router.get("/api/pnl/by-algo")
    async def get_pnl_by_algo() -> JSONResponse:
        async def _produce() -> str:
            from trading.reports.trades import fetch_filled_trades, summarize_by_algo

            today = _today_start()
            trades = await fetch_filled_trades(session_factory, start=today, end=clock.now())
            by_algo = summarize_by_algo(trades)
            return json.dumps({
                name: {
                    "gross": round(s.gross, 2),
                    "costs": round(s.costs, 2),
                    "net": round(s.net, 2),
                }
                for name, s in by_algo.items()
            })

        if cacher_factory is not None:
            today_iso = clock.now().date().isoformat()
            body = await cacher_factory.api().get_or_set_response(  # type: ignore[reportUnknownMemberType]
                key_args=("pnl:by_algo", today_iso),
                producer=_produce,
                ttl=30,
            )
            return JSONResponse(content=json.loads(body))
        return JSONResponse(content=json.loads(await _produce()))

    return router
