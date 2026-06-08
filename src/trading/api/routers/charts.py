from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.clock import Clock
from trading.strategy.storage.models import AlgoConfig
from trading.strategy.storage.store import ChartStore


def create_charts_router(
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
) -> APIRouter:
    router = APIRouter()

    def _today_start() -> datetime:
        today = clock.today()
        return datetime(today.year, today.month, today.day, tzinfo=clock.tz).astimezone(UTC)

    @router.get("/api/charts")
    async def get_charts(
        session_id: str = "", algo_name: str = "", limit: int = 500
    ) -> JSONResponse:
        chart_store = ChartStore(session_factory)
        sid: str | None = session_id if session_id else None
        since = _today_start()

        async with session_factory() as session:
            result = await session.execute(select(AlgoConfig.name))
            all_algo_names = [r[0] for r in result.fetchall()]

        algo_names = [algo_name] if algo_name else all_algo_names

        combined: dict[str, dict[str, list[dict[str, object]]]] = {}
        for name in algo_names:
            chart_names = await chart_store.get_chart_names(name, since, sid)
            for chart_name in chart_names:
                series = await chart_store.get_indicator_series(
                    name, chart_name, since, sid, limit
                )
                if chart_name not in combined:
                    combined[chart_name] = {}
                combined[chart_name].update(series)

        return JSONResponse(content=combined)

    return router
