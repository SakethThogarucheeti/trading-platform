"""Tests for PnlCacher."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from trading.core.clock import SYSTEM_CLOCK
from trading.core.schemas import Side
from trading.storage.cache.backend import ValueCache, setup_cache, _backend
from trading.storage.cache.pnl import PnlCacher


@pytest.fixture(autouse=True)
def _reset_backend():
    setup_cache(None)
    yield
    try:
        _backend._state = {}  # type: ignore[attr-defined]
    except AttributeError:
        pass


@pytest.fixture
def cache() -> ValueCache:
    return ValueCache()


@pytest.fixture
def pnl(cache: ValueCache) -> PnlCacher:
    return PnlCacher(cache, SYSTEM_CLOCK)


TODAY = date(2026, 1, 15)


class TestPnlCacherKeyAndTtl:
    def test_make_key(self, pnl: PnlCacher) -> None:
        assert pnl.make_key(TODAY) == "rf:pnl:2026-01-15"

    def test_default_ttl_is_positive_and_reasonable(self, pnl: PnlCacher) -> None:
        ttl = pnl.default_ttl()
        assert 3600 < ttl <= 90_000  # at least 1h (grace) and at most ~25h

    def test_different_dates_produce_different_keys(self, pnl: PnlCacher) -> None:
        d1 = date(2026, 1, 15)
        d2 = date(2026, 1, 16)
        assert pnl.make_key(d1) != pnl.make_key(d2)


class TestPnlCacherGetOrSet:
    @pytest.mark.asyncio
    async def test_cache_miss_calls_producer(self, pnl: PnlCacher) -> None:
        producer = AsyncMock(return_value=500.0)
        result = await pnl.get_or_set((TODAY,), producer=producer)
        assert result == 500.0
        producer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cache_hit_skips_producer(self, pnl: PnlCacher) -> None:
        producer = AsyncMock(return_value=500.0)
        await pnl.get_or_set((TODAY,), producer=producer)

        producer2 = AsyncMock(return_value=999.0)
        result = await pnl.get_or_set((TODAY,), producer=producer2)
        assert result == 500.0
        producer2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_different_dates_are_isolated(self, pnl: PnlCacher) -> None:
        d1 = date(2026, 1, 15)
        d2 = date(2026, 1, 16)
        await pnl.get_or_set((d1,), producer=AsyncMock(return_value=100.0))
        result = await pnl.get_or_set((d2,), producer=AsyncMock(return_value=200.0))
        assert result == 200.0


class TestPnlCacherIncrementSync:
    def test_increment_sell_increases_pnl(self, pnl: PnlCacher) -> None:
        pnl.increment_sync(TODAY, Side.SELL, avg_price=100.0, qty=10)
        assert pnl._cache.get_sync(pnl.make_key(TODAY)) == pytest.approx(1000.0)

    def test_increment_buy_decreases_pnl(self, pnl: PnlCacher) -> None:
        pnl.increment_sync(TODAY, Side.BUY, avg_price=100.0, qty=10)
        assert pnl._cache.get_sync(pnl.make_key(TODAY)) == pytest.approx(-1000.0)

    def test_multiple_increments_accumulate(self, pnl: PnlCacher) -> None:
        pnl.increment_sync(TODAY, Side.SELL, avg_price=100.0, qty=10)  # +1000
        pnl.increment_sync(TODAY, Side.BUY, avg_price=50.0, qty=5)    # -250
        assert pnl._cache.get_sync(pnl.make_key(TODAY)) == pytest.approx(750.0)

    def test_increment_starts_from_zero_if_no_prior_value(self, pnl: PnlCacher) -> None:
        pnl.increment_sync(TODAY, Side.SELL, avg_price=200.0, qty=3)
        assert pnl._cache.get_sync(pnl.make_key(TODAY)) == pytest.approx(600.0)

    @pytest.mark.asyncio
    async def test_increment_sync_value_visible_via_async_get(self, pnl: PnlCacher) -> None:
        pnl.increment_sync(TODAY, Side.SELL, avg_price=100.0, qty=5)
        # Async get reads memory (populated by set_sync) without hitting Redis
        result = await pnl._cache.get(pnl.make_key(TODAY))
        assert result == pytest.approx(500.0)
