"""Tests for ApiResponseCacher."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock

import pytest

from trading.storage.cache.api import ApiResponseCacher
from trading.storage.cache.backend import ValueCache, setup_cache, _backend


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
def cacher(cache: ValueCache) -> ApiResponseCacher:
    return ApiResponseCacher(cache)


TODAY = date(2026, 1, 15)


class TestApiResponseCacherKey:
    def test_make_key_single_arg(self, cacher: ApiResponseCacher) -> None:
        assert cacher.make_key("pnl") == "api:pnl"

    def test_make_key_multiple_args(self, cacher: ApiResponseCacher) -> None:
        assert cacher.make_key("pnl", "2026-01-15") == "api:pnl:2026-01-15"


class TestGetOrSetResponse:
    @pytest.mark.asyncio
    async def test_miss_calls_producer(self, cacher: ApiResponseCacher) -> None:
        body = json.dumps({"pnl": 100})
        producer = AsyncMock(return_value=body)
        result = await cacher.get_or_set_response(("pnl", "2026-01-15"), producer=producer, ttl=30)
        assert result == body
        producer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hit_skips_producer(self, cacher: ApiResponseCacher) -> None:
        body = json.dumps({"pnl": 100})
        producer1 = AsyncMock(return_value=body)
        await cacher.get_or_set_response(("pnl", "2026-01-15"), producer=producer1, ttl=30)

        producer2 = AsyncMock(return_value=json.dumps({"pnl": 999}))
        result = await cacher.get_or_set_response(("pnl", "2026-01-15"), producer=producer2, ttl=30)
        assert result == body
        producer2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_different_key_args_are_isolated(self, cacher: ApiResponseCacher) -> None:
        d1 = "2026-01-15"
        d2 = "2026-01-16"
        body1 = json.dumps({"pnl": 100})
        body2 = json.dumps({"pnl": 200})
        await cacher.get_or_set_response(("pnl", d1), producer=AsyncMock(return_value=body1), ttl=30)
        result = await cacher.get_or_set_response(("pnl", d2), producer=AsyncMock(return_value=body2), ttl=30)
        assert result == body2


class TestInvalidatePnl:
    @pytest.mark.asyncio
    async def test_invalidate_pnl_clears_both_keys(self, cacher: ApiResponseCacher) -> None:
        body = json.dumps({"pnl": 100})
        await cacher.get_or_set_response(("pnl", TODAY.isoformat()), producer=AsyncMock(return_value=body), ttl=30)
        await cacher.get_or_set_response(("pnl:by_algo", TODAY.isoformat()), producer=AsyncMock(return_value=body), ttl=30)

        await cacher.invalidate_pnl(TODAY)

        producer = AsyncMock(return_value=json.dumps({"pnl": 999}))
        result = await cacher.get_or_set_response(("pnl", TODAY.isoformat()), producer=producer, ttl=30)
        assert result == json.dumps({"pnl": 999})
        producer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalidate_pnl_by_algo_cleared(self, cacher: ApiResponseCacher) -> None:
        body = json.dumps({"algo": "ema"})
        await cacher.get_or_set_response(("pnl:by_algo", TODAY.isoformat()), producer=AsyncMock(return_value=body), ttl=30)

        await cacher.invalidate_pnl(TODAY)

        producer = AsyncMock(return_value=json.dumps({"algo": "rsi"}))
        result = await cacher.get_or_set_response(("pnl:by_algo", TODAY.isoformat()), producer=producer, ttl=30)
        assert result == json.dumps({"algo": "rsi"})
        producer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalidate_pnl_does_not_affect_other_keys(self, cacher: ApiResponseCacher) -> None:
        report_body = json.dumps({"report": "data"})
        await cacher.get_or_set_response(("report", "monthly"), producer=AsyncMock(return_value=report_body), ttl=60)

        await cacher.invalidate_pnl(TODAY)

        producer = AsyncMock(return_value=json.dumps({"report": "new"}))
        result = await cacher.get_or_set_response(("report", "monthly"), producer=producer, ttl=60)
        assert result == report_body  # still cached
        producer.assert_not_awaited()
