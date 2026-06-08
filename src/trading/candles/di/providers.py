from __future__ import annotations

from dishka import Provider, Scope, provide  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.candles.service.aggregator import CandleAggregator
from trading.candles.service.historical import HistoricalDataService
from trading.candles.service.persister import CandleConfig, CandlePersister
from trading.candles.storage.store import CandleDataStore
from trading.core.models import Instrument
from trading.tick_ingest.storage.store import AuditStore


class CandlesProvider(Provider):
    """Wires candles module internals."""

    scope = Scope.APP

    @provide
    def candle_store(self, sf: async_sessionmaker[AsyncSession]) -> CandleDataStore:
        return CandleDataStore(sf)

    @provide
    async def candle_aggregator(
        self,
        candle: CandleDataStore,
        audit: AuditStore,
        sf: async_sessionmaker[AsyncSession],
        settings: object,
    ) -> CandleAggregator:
        from sqlalchemy import select
        from trading.config.settings import Settings

        s: Settings = settings  # type: ignore[assignment]
        async with sf() as session:
            instruments = list((await session.execute(select(Instrument))).scalars().all())

        config = CandleConfig(
            instruments=instruments,
            intervals=s.candle_intervals,
            warmup_count=s.warmup_candles,
        )
        return CandleAggregator(config=config, candle_logger=CandlePersister(candle, audit))
