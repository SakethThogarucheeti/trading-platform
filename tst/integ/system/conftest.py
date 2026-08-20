from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.app.database import build_session_factory, init_db


@pytest.fixture(scope="session")
def pg_container():
    """Start a real Postgres container for the test session."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest_asyncio.fixture
async def engine(pg_container) -> AsyncEngine:
    """
    Async engine connected to the test Postgres container.

    Tables are created on first use (init_db is idempotent).
    All rows are truncated after each test so tests don't share state.
    """
    url = (
        pg_container.get_connection_url()
        .replace("psycopg2", "asyncpg")
        .replace("postgresql://", "postgresql+asyncpg://")
    )
    eng = create_async_engine(url, echo=False)
    await init_db(eng)
    yield eng
    from sqlalchemy import text

    async with eng.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE orders, signals, positions, instruments, "
                "heartbeats, audit_logs, decision_logs, tick_logs, "
                "algo_state, algo_configs, candles CASCADE"
            )
        )
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    """Async session factory for the test database."""
    return build_session_factory(engine)


@pytest.fixture(scope="session")
def redis_container():
    """Start a real Redis container for the test session."""
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as redis:
        yield redis


@pytest_asyncio.fixture
async def redis_url(redis_container) -> str:
    """redis:// URL for the test Redis container, flushed clean per test."""
    import redis.asyncio as aioredis

    url = f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}"
    yield url
    client = aioredis.from_url(url)
    await client.flushall()
    await client.aclose()
