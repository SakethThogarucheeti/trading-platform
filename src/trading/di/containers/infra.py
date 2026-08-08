from __future__ import annotations

from collections.abc import AsyncIterator

from dependency_injector import containers, providers
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from trading.app.database import build_engine, build_session_factory
from trading.broker.service.paper_broker import PriceStore
from trading.candles.storage.store import CandleDataStore, InstrumentStore
from trading.config.settings import Settings
from trading.core.clock import Clock, SystemClock
from trading.execution.storage.store import PositionStore, TradingStore
from trading.monitoring.storage.store import HeartbeatStore
from trading.storage.cache import CacherFactory, ValueCache, setup_cache
from trading.strategy.storage.store import ChartStore, ConfigStore
from trading.tick_ingest.storage.store import AuditStore


async def _redis_client(settings: Settings) -> AsyncIterator[object]:
    if not settings.redis_url:
        yield None
        return
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    client = aioredis.Redis.from_url(settings.redis_url, decode_responses=False)  # type: ignore[reportUnknownMemberType]
    try:
        yield client
    finally:
        await client.aclose()


def _clock(settings: Settings) -> Clock:
    return SystemClock(timezone=settings.timezone)


async def _db_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = build_engine(str(settings.postgres_url))
    yield engine
    await engine.dispose()


def _price_store(settings: Settings) -> PriceStore:
    return PriceStore(slippage_pct=settings.paper_slippage_pct / 100)


def _value_cache(settings: Settings) -> ValueCache:
    setup_cache(settings.redis_url)
    return ValueCache()


class InfrastructureContainer(containers.DeclarativeContainer):
    """
    Singletons that live for the entire process lifetime.

    Provides: AsyncEngine, async_sessionmaker, domain stores, PriceStore,
    redis client. Takes `settings` as an external Dependency -- the owning
    AppContainer supplies its own `settings` Singleton when composing this
    container in, so Settings itself is only ever constructed once.
    """

    settings = providers.Dependency(instance_of=Settings)

    clock = providers.Singleton(_clock, settings=settings)

    db_engine: providers.Provider[AsyncEngine] = providers.Resource(_db_engine, settings=settings)

    session_factory: providers.Provider[async_sessionmaker[AsyncSession]] = providers.Singleton(
        build_session_factory, engine=db_engine
    )

    candle_data_store = providers.Singleton(CandleDataStore, session_factory)
    instrument_store = providers.Singleton(InstrumentStore, session_factory)
    trading_store = providers.Singleton(TradingStore, session_factory)
    position_store = providers.Singleton(PositionStore, session_factory)
    audit_store = providers.Singleton(AuditStore, session_factory)
    heartbeat_store = providers.Singleton(HeartbeatStore, session_factory)
    config_store = providers.Singleton(ConfigStore, session_factory)
    chart_store = providers.Singleton(ChartStore, session_factory)

    price_store = providers.Singleton(_price_store, settings=settings)

    value_cache = providers.Singleton(_value_cache, settings=settings)

    cacher_factory = providers.Singleton(CacherFactory, value_cache, clock)

    redis_client = providers.Resource(_redis_client, settings=settings)
