"""Integration tests for CandleStore + CandleDataStore (Postgres round-trip)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from trading.storage.stores.candle_store import CandleStore
from trading.storage.stores.candle import CandleDataStore


@pytest.fixture(scope="session")
def pg_container():
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed")
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture
async def pg_engine(pg_container):
    from sqlalchemy.ext.asyncio import create_async_engine

    from trading.core.database import init_db

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
        await conn.execute(text("TRUNCATE TABLE candles CASCADE"))
    await eng.dispose()


@pytest.mark.asyncio
async def test_save_and_get_candles(pg_engine) -> None:
    sf = async_sessionmaker(pg_engine, expire_on_commit=False)
    candle_store = CandleDataStore(sf)

    rows = [
        {
            "symbol": "INFY",
            "interval": "15min",
            "ts": datetime(2024, 1, 2, 9, 15, tzinfo=UTC),
            "open": 1500.0,
            "high": 1510.0,
            "low": 1495.0,
            "close": 1505.0,
            "volume": 10000,
        },
        {
            "symbol": "INFY",
            "interval": "15min",
            "ts": datetime(2024, 1, 2, 9, 30, tzinfo=UTC),
            "open": 1505.0,
            "high": 1520.0,
            "low": 1500.0,
            "close": 1515.0,
            "volume": 12000,
        },
    ]
    await candle_store.save_candles(rows)
    result = await candle_store.get_candles("INFY", "15min", limit=10)

    assert len(result) == 2
    assert result[0]["close"] == pytest.approx(1505.0)
    assert result[1]["close"] == pytest.approx(1515.0)
    assert result[0]["ts"] < result[1]["ts"]


@pytest.mark.asyncio
async def test_save_candles_idempotent(pg_engine) -> None:
    sf = async_sessionmaker(pg_engine, expire_on_commit=False)
    candle_store = CandleDataStore(sf)

    row = {
        "symbol": "TCS",
        "interval": "1min",
        "ts": datetime(2024, 1, 3, 9, 15, tzinfo=UTC),
        "open": 3000.0,
        "high": 3010.0,
        "low": 2995.0,
        "close": 3005.0,
        "volume": 5000,
    }

    await candle_store.save_candles([row])
    await candle_store.save_candles([row])

    result = await candle_store.get_candles("TCS", "1min", limit=10)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_get_candles_since(pg_engine) -> None:
    sf = async_sessionmaker(pg_engine, expire_on_commit=False)
    candle_store = CandleDataStore(sf)

    base = datetime(2024, 1, 4, 9, 0, tzinfo=UTC)
    rows = [
        {
            "symbol": "RELIANCE",
            "interval": "15min",
            "ts": base + timedelta(minutes=15 * i),
            "open": 2000.0,
            "high": 2010.0,
            "low": 1995.0,
            "close": 2005.0,
            "volume": 8000,
        }
        for i in range(10)
    ]
    await candle_store.save_candles(rows)

    since = base + timedelta(minutes=15 * 5)
    result = await candle_store.get_candles_since("RELIANCE", "15min", since)

    assert len(result) == 5


@pytest.mark.asyncio
async def test_candle_store_end_to_end(pg_engine) -> None:
    from quantindicators.library.ema import EMA

    sf = async_sessionmaker(pg_engine, expire_on_commit=False)
    candle_store = CandleDataStore(sf)

    base = datetime(2024, 1, 5, 9, 15, tzinfo=UTC)
    rows = [
        {
            "symbol": "HDFC",
            "interval": "15min",
            "ts": base + timedelta(minutes=15 * i),
            "open": 200.0,
            "high": 201.0,
            "low": 199.0,
            "close": 200.0,
            "volume": 1000,
        }
        for i in range(30)
    ]
    await candle_store.save_candles(rows)

    store = CandleStore(candle_store=candle_store)
    ema = EMA(store, "HDFC", "15min")
    result = await ema.compute(EMA.Parameters(period=9))
    assert result == pytest.approx(200.0, rel=1e-3)


@pytest.mark.asyncio
async def test_candle_store_redis_cache(pg_engine) -> None:
    import fakeredis.aioredis as fakeredis

    sf = async_sessionmaker(pg_engine, expire_on_commit=False)
    candle_store = CandleDataStore(sf)
    redis = fakeredis.FakeRedis()

    base = datetime(2024, 1, 6, 9, 15, tzinfo=UTC)
    rows = [
        {
            "symbol": "WIPRO",
            "interval": "15min",
            "ts": base + timedelta(minutes=15 * i),
            "open": 300.0,
            "high": 301.0,
            "low": 299.0,
            "close": 300.0,
            "volume": 500,
        }
        for i in range(20)
    ]
    await candle_store.save_candles(rows)

    store = CandleStore(candle_store=candle_store, redis=redis)
    r1 = await store.fetch("WIPRO", "15min", 20)
    r2 = await store.fetch("WIPRO", "15min", 20)

    assert len(r1) == len(r2) == 20
    assert r1[0]["close"] == r2[0]["close"]
    keys = await redis.keys("cs:candles:WIPRO:15min:*")
    assert len(keys) == 1
