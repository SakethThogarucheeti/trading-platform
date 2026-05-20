from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from trading.core.models import Base


def build_engine(url: str) -> AsyncEngine:
    """Create an async engine from a connection URL."""
    is_postgres = "postgresql" in url or "postgres" in url
    kwargs: dict[str, object] = dict(echo=False, pool_pre_ping=True, pool_recycle=1800)
    if is_postgres:
        kwargs["pool_size"] = 10       # enough for concurrent background fire() tasks
        kwargs["max_overflow"] = 5
        kwargs["connect_args"] = {
            "server_settings": {"application_name": "algo-trader"},
        }
    return create_async_engine(url, **kwargs)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to the given engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session(
    engine: AsyncEngine,
) -> AsyncIterator[AsyncSession]:
    """
    Yield a transactional AsyncSession.

    Commits on clean exit, rolls back on exception. Intended for use
    with `async with get_session(engine) as session:`.
    """
    factory = build_session_factory(engine)
    async with factory() as session:
        async with session.begin():
            yield session


async def init_db(engine: AsyncEngine) -> None:
    """
    Create all tables from ORM metadata.

    Only used in tests and development. Production uses Alembic migrations.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_db(engine: AsyncEngine) -> None:
    """Drop all tables. Tests only."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
