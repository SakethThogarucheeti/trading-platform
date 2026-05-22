"""Unit tests for CandleStore Redis error paths and fetch_since (no real DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading.storage.stores.candle_store import CandleStore


def _pg_candle_store(rows=None):
    """Return a mock AbstractCandleDataStore that returns the given rows."""
    rows = rows or []
    mock = MagicMock()
    mock.get_candles = AsyncMock(return_value=rows)
    mock.get_candles_since = AsyncMock(return_value=rows)
    return mock


def _redis(cached=None, raise_on_get=False, raise_on_setex=False):
    r = MagicMock()
    if raise_on_get:
        r.get = AsyncMock(side_effect=ConnectionError("redis down"))
    else:
        r.get = AsyncMock(return_value=cached)
    if raise_on_setex:
        r.setex = AsyncMock(side_effect=ConnectionError("redis down"))
    else:
        r.setex = AsyncMock()
    return r


@pytest.mark.anyio
async def test_fetch_since_without_redis() -> None:
    rows = [{"symbol": "INFY", "interval": "5min", "ts": "2024-01-02T09:15:00+00:00", "close": 1505.0}]
    store = CandleStore(candle_store=_pg_candle_store(rows))
    since = datetime(2024, 1, 2, 9, 0, tzinfo=UTC)
    result = await store.fetch_since("INFY", "5min", since)
    assert result == rows


@pytest.mark.anyio
async def test_fetch_since_with_redis_cache_hit() -> None:
    import json

    rows = [{"symbol": "INFY", "close": 1505.0}]
    cached = json.dumps(rows)
    redis = _redis(cached=cached.encode())
    store = CandleStore(candle_store=_pg_candle_store(), redis=redis)
    since = datetime(2024, 1, 2, 9, 0, tzinfo=UTC)
    result = await store.fetch_since("INFY", "5min", since)
    assert result == rows
    # DB was not hit
    store._candle.get_candles_since.assert_not_awaited()


@pytest.mark.anyio
async def test_fetch_since_with_redis_cache_miss_stores_result() -> None:
    rows = [{"symbol": "INFY", "close": 1505.0}]
    redis = _redis(cached=None)
    store = CandleStore(candle_store=_pg_candle_store(rows), redis=redis)
    since = datetime(2024, 1, 2, 9, 0, tzinfo=UTC)
    result = await store.fetch_since("INFY", "5min", since)
    assert result == rows
    redis.setex.assert_awaited_once()
    # Key should include 'since' timestamp
    key = redis.setex.call_args.args[0]
    assert "since" in key


@pytest.mark.anyio
async def test_fetch_redis_get_error_falls_back_to_db() -> None:
    rows = [{"symbol": "INFY", "close": 1505.0}]
    redis = _redis(raise_on_get=True)
    store = CandleStore(candle_store=_pg_candle_store(rows), redis=redis)
    result = await store.fetch("INFY", "5min", 10)
    assert result == rows  # DB fallback


@pytest.mark.anyio
async def test_fetch_redis_setex_error_does_not_raise() -> None:
    rows = [{"symbol": "INFY", "close": 1505.0}]
    redis = _redis(cached=None, raise_on_setex=True)
    store = CandleStore(candle_store=_pg_candle_store(rows), redis=redis)
    result = await store.fetch("INFY", "5min", 10)
    assert result == rows  # still returns DB result


@pytest.mark.anyio
async def test_fetch_no_redis_setex_when_rows_empty() -> None:
    redis = _redis(cached=None)
    store = CandleStore(candle_store=_pg_candle_store([]), redis=redis)
    result = await store.fetch("INFY", "5min", 10)
    assert result == []
    redis.setex.assert_not_awaited()  # empty rows → no cache write
