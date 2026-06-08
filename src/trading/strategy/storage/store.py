from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.strategy.storage.models import AlgoConfig, AlgoState, IndicatorLog


class ChartStore:
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


class ConfigStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def seed_algo_config(
        self,
        name: str,
        strategy_id: str,
        warmup_candles: int,
        candle_intervals: list[str],
        equity: float,
        params: dict[str, object],
    ) -> None:
        async with self._sf() as session:
            async with session.begin():
                existing = await session.get(AlgoConfig, name)
                if existing is None:
                    session.add(
                        AlgoConfig(
                            name=name,
                            strategy_id=strategy_id,
                            warmup_candles=warmup_candles,
                            candle_intervals=json.dumps(candle_intervals),
                            equity=equity,
                            params=json.dumps(params),
                        )
                    )
                else:
                    existing.params = json.dumps(params)

    async def upsert_algo_state(self, name: str, state: dict[str, object]) -> None:
        async with self._sf() as session:
            async with session.begin():
                existing = await session.get(AlgoState, name)
                if existing is None:
                    session.add(AlgoState(name=name, state=json.dumps(state)))
                else:
                    existing.state = json.dumps(state)
                    existing.updated_at = datetime.now(UTC)

    async def get_algo_configs_with_state(self) -> list[dict[str, object]]:
        async with self._sf() as session:
            result = await session.execute(select(AlgoConfig))
            configs = result.scalars().all()
            names = [c.name for c in configs]
            states_result = await session.execute(
                select(AlgoState).where(AlgoState.name.in_(names))
            )
            state_map = {s.name: s for s in states_result.scalars().all()}

        out: list[dict[str, object]] = []
        for cfg in configs:
            state_obj = state_map.get(cfg.name)
            state: dict[str, object] = json.loads(state_obj.state) if state_obj else {}
            out.append(
                {
                    "name": cfg.name,
                    "strategy_id": cfg.strategy_id,
                    "warmup_candles": cfg.warmup_candles,
                    "candle_intervals": json.loads(cfg.candle_intervals),
                    "equity": cfg.equity,
                    "enabled": cfg.enabled,
                    "params": json.loads(cfg.params),
                    "state": state,
                    "updated_at": state_obj.updated_at.isoformat() if state_obj else None,
                }
            )
        return out
