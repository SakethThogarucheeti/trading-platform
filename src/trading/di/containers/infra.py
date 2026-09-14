from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from trading.app.database import build_engine, build_session_factory
from trading.broker.service.paper_broker import PriceStore
from trading.candles.storage.store import CandleDataStore, InstrumentStore
from trading.config.settings import Settings
from trading.core.clock import Clock, SystemClock
from trading.execution.storage.store import PositionStore, TradingStore
from trading.monitoring.storage.store import HeartbeatStore
from trading.storage.cache import CacherFactory, ValueCache
from trading.storage.cache.postgres_backend import PostgresKVCache
from trading.strategy.storage.store import ChartStore, ConfigStore
from trading.tick_ingest.storage.store import AuditStore


@dataclass
class Infra:
    """Singletons that live for the entire process lifetime."""

    settings: Settings
    clock: Clock
    db_engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    candle_data_store: CandleDataStore
    instrument_store: InstrumentStore
    trading_store: TradingStore
    position_store: PositionStore
    audit_store: AuditStore
    heartbeat_store: HeartbeatStore
    config_store: ConfigStore
    chart_store: ChartStore
    price_store: PriceStore
    value_cache: ValueCache
    cacher_factory: CacherFactory


async def build_infra(settings: Settings, engine: AsyncEngine | None = None) -> Infra:
    """
    Build the process-lifetime infrastructure singletons.

    `engine` lets a caller substitute a fake (e.g. an in-memory sqlite engine
    in tests) instead of a live Postgres connection -- the one override
    tst/unit/di/test_container.py actually needs (trading-platform#3 removed
    the dependency_injector container hierarchy and its .override() mechanism
    this used to go through).
    """
    db_engine = engine if engine is not None else build_engine(str(settings.postgres_url))
    session_factory = build_session_factory(db_engine)
    clock = SystemClock(timezone=settings.timezone)
    value_cache = ValueCache()

    return Infra(
        settings=settings,
        clock=clock,
        db_engine=db_engine,
        session_factory=session_factory,
        candle_data_store=CandleDataStore(session_factory),
        instrument_store=InstrumentStore(session_factory),
        trading_store=TradingStore(session_factory),
        position_store=PositionStore(session_factory),
        audit_store=AuditStore(session_factory),
        heartbeat_store=HeartbeatStore(session_factory),
        config_store=ConfigStore(session_factory),
        chart_store=ChartStore(session_factory),
        price_store=PriceStore(slippage_pct=settings.paper_slippage_pct / 100),
        value_cache=value_cache,
        # rolling_state_cache is durable (Postgres-backed) so SignalGenerator's
        # in-progress strategy state survives a process restart --
        # trading-platform#79; api() stays on the in-memory value_cache.
        cacher_factory=CacherFactory(
            value_cache, clock, rolling_state_cache=PostgresKVCache(session_factory)
        ),
    )
