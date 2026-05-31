from __future__ import annotations

from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from trading.broker.paper_broker import AbstractPriceStore, PriceStore
from trading.config.settings import Settings, get_settings
from trading.core.clock import Clock, SystemClock
from trading.core.database import build_engine, build_session_factory
from trading.storage.cache import CacherFactory, ValueCache, setup_cache
from trading.storage.stores.audit import AuditStore
from trading.storage.stores.candle import CandleDataStore
from trading.storage.stores.chart import ChartStore
from trading.storage.stores.config import ConfigStore
from trading.storage.stores.heartbeat import HeartbeatStore
from trading.storage.stores.instrument import InstrumentStore
from trading.storage.stores.position import PositionStore
from trading.storage.stores.trading import TradingStore


class RedisProvider(Provider):
    """Provides a redis.asyncio.Redis client for pub/sub and caching."""

    scope = Scope.APP

    @provide
    async def redis_client(self, settings: Settings) -> AsyncIterator[object]:
        if not settings.redis_url:
            yield None
            return
        import redis.asyncio as aioredis  # type: ignore[import-untyped]

        client = aioredis.Redis.from_url(settings.redis_url, decode_responses=False)  # type: ignore[reportUnknownMemberType]
        try:
            yield client
        finally:
            await client.aclose()


class InfrastructureProvider(Provider):
    """
    Singletons that live for the entire process lifetime.

    Provides: Settings, AsyncEngine, async_sessionmaker, domain stores, PriceStore.
    """

    scope = Scope.APP

    @provide
    def settings(self) -> Settings:
        return get_settings()

    @provide
    def clock(self, settings: Settings) -> Clock:
        return SystemClock(timezone=settings.timezone)

    @provide
    async def db_engine(self, settings: Settings) -> AsyncIterator[AsyncEngine]:
        engine = build_engine(str(settings.postgres_url))
        yield engine
        await engine.dispose()

    @provide
    def session_factory(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return build_session_factory(engine)

    @provide
    def candle_data_store(self, sf: async_sessionmaker[AsyncSession]) -> CandleDataStore:
        return CandleDataStore(sf)

    @provide
    def instrument_store(self, sf: async_sessionmaker[AsyncSession]) -> InstrumentStore:
        return InstrumentStore(sf)

    @provide
    def trading_store(self, sf: async_sessionmaker[AsyncSession]) -> TradingStore:
        return TradingStore(sf)

    @provide
    def position_store(self, sf: async_sessionmaker[AsyncSession]) -> PositionStore:
        return PositionStore(sf)

    @provide
    def audit_store(self, sf: async_sessionmaker[AsyncSession]) -> AuditStore:
        return AuditStore(sf)

    @provide
    def heartbeat_store(self, sf: async_sessionmaker[AsyncSession]) -> HeartbeatStore:
        return HeartbeatStore(sf)

    @provide
    def config_store(self, sf: async_sessionmaker[AsyncSession]) -> ConfigStore:
        return ConfigStore(sf)

    @provide
    def chart_store(self, sf: async_sessionmaker[AsyncSession]) -> ChartStore:
        return ChartStore(sf)

    @provide
    def price_store(self, settings: Settings) -> AbstractPriceStore:
        return PriceStore(slippage_pct=settings.paper_slippage_pct / 100)

    @provide
    def value_cache(self, settings: Settings) -> ValueCache:
        setup_cache(settings.redis_url)
        return ValueCache()

    @provide
    def cacher_factory(self, cache: ValueCache, clock: Clock) -> CacherFactory:
        return CacherFactory(cache, clock)
