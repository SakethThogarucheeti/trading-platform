from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.models import IndicatorLog


class AbstractChartStore(ABC):
    @abstractmethod
    async def log_indicator(
        self,
        algo_name: str,
        symbol: str,
        interval: str,
        chart: str,
        series: str,
        ts: datetime,
        value: float,
        session_id: str | None = None,
    ) -> None: ...

    @abstractmethod
    async def get_chart_names(
        self,
        algo_name: str,
        since: datetime,
        session_id: str | None = None,
    ) -> list[str]: ...

    @abstractmethod
    async def get_indicator_series(
        self,
        algo_name: str,
        chart: str,
        since: datetime,
        session_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, list[dict[str, object]]]: ...


class ChartStore(AbstractChartStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def log_indicator(
        self,
        algo_name: str,
        symbol: str,
        interval: str,
        chart: str,
        series: str,
        ts: datetime,
        value: float,
        session_id: str | None = None,
    ) -> None:
        async with self._sf() as session:
            async with session.begin():
                session.add(
                    IndicatorLog(
                        algo_name=algo_name,
                        session_id=session_id,
                        symbol=symbol,
                        interval=interval,
                        chart=chart,
                        series=series,
                        ts=ts,
                        value=value,
                    )
                )

    async def get_chart_names(
        self,
        algo_name: str,
        since: datetime,
        session_id: str | None = None,
    ) -> list[str]:
        stmt = (
            select(IndicatorLog.chart)
            .distinct()
            .where(
                IndicatorLog.algo_name == algo_name,
                IndicatorLog.ts >= since,
                IndicatorLog.session_id.is_(None)
                if session_id is None
                else IndicatorLog.session_id == session_id,
            )
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [r[0] for r in result.fetchall()]

    async def get_indicator_series(
        self,
        algo_name: str,
        chart: str,
        since: datetime,
        session_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, list[dict[str, object]]]:
        stmt = (
            select(IndicatorLog)
            .where(
                IndicatorLog.algo_name == algo_name,
                IndicatorLog.chart == chart,
                IndicatorLog.ts >= since,
                IndicatorLog.session_id.is_(None)
                if session_id is None
                else IndicatorLog.session_id == session_id,
            )
            .order_by(IndicatorLog.ts.asc())
            .limit(limit)
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            out: dict[str, list[dict[str, object]]] = {}
            for row in result.scalars().all():
                out.setdefault(row.series, []).append(
                    {"ts": row.ts.isoformat(), "value": row.value}
                )
        return out
