from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Importing every per-module models module (for side effects) is what registers
# each module's model classes -- including the classes some cross-module
# relationships reference by string -- against the one shared registry
# (trading-platform#35/#103) before init_db()/drop_db() below act on its metadata.
import trading.broker.storage.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
import trading.candles.storage.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
import trading.execution.storage.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
import trading.monitoring.storage.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
import trading.risk.storage.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
import trading.storage.cache.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
import trading.strategy.storage.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
import trading.tick_ingest.storage.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from trading.core.db_registry import shared_registry


def build_engine(url: str) -> AsyncEngine:
    """Create an async engine from a connection URL."""
    is_postgres = "postgresql" in url or "postgres" in url
    kwargs: dict[str, object] = dict(echo=False, pool_pre_ping=True, pool_recycle=1800)
    if is_postgres:
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 5
        kwargs["connect_args"] = {
            "server_settings": {"application_name": "algo-trader"},
        }
    return create_async_engine(url, **kwargs)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to the given engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Yield a transactional AsyncSession; commits on exit, rolls back on exception."""
    factory = build_session_factory(engine)
    async with factory() as session:
        async with session.begin():
            yield session


async def init_db(engine: AsyncEngine) -> None:
    """
    Create all tables from every module's ORM metadata.

    Only used in tests and development. Production uses Alembic migrations.
    """
    async with engine.begin() as conn:
        await conn.run_sync(shared_registry.metadata.create_all)


async def drop_db(engine: AsyncEngine) -> None:
    """Drop all tables across all modules. Tests only."""
    async with engine.begin() as conn:
        await conn.run_sync(shared_registry.metadata.drop_all)
