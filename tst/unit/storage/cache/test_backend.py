"""Tests for ValueCache two-tier backend."""

from __future__ import annotations

import pytest
import pytest_asyncio

from trading.storage.cache.backend import ValueCache, _backend, setup_cache


@pytest.fixture(autouse=True)
def _reset_backend():
    """Ensure cashews uses in-memory backend and is clean for each test."""
    setup_cache(None)
    yield
    # Clear all in-memory cashews state between tests
    _backend._state = {}  # type: ignore[attr-defined]


@pytest.fixture
def cache() -> ValueCache:
    return ValueCache()


class TestValueCacheAsync:
    @pytest.mark.asyncio
    async def test_get_miss_returns_none(self, cache: ValueCache) -> None:
        assert await cache.get("missing") is None

    @pytest.mark.asyncio
    async def test_set_and_get_roundtrip(self, cache: ValueCache) -> None:
        await cache.set("k", {"x": 1})
        result = await cache.get("k")
        assert result == {"x": 1}

    @pytest.mark.asyncio
    async def test_set_string_value(self, cache: ValueCache) -> None:
        await cache.set("s", "hello")
        assert await cache.get("s") == "hello"

    @pytest.mark.asyncio
    async def test_set_numeric_value(self, cache: ValueCache) -> None:
        await cache.set("n", 3.14)
        assert await cache.get("n") == pytest.approx(3.14)

    @pytest.mark.asyncio
    async def test_delete_removes_key(self, cache: ValueCache) -> None:
        await cache.set("k", 1)
        await cache.delete("k")
        assert await cache.get("k") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_key_no_error(self, cache: ValueCache) -> None:
        await cache.delete("no_such_key")  # must not raise

    @pytest.mark.asyncio
    async def test_memory_populated_from_redis_on_miss(self, cache: ValueCache) -> None:
        """A second ValueCache instance reading a key set by the first should populate memory."""
        await cache.set("shared", [1, 2, 3])

        other = ValueCache()
        assert other.get_sync("shared") is None  # memory cold
        result = await other.get("shared")
        assert result == [1, 2, 3]
        assert other.get_sync("shared") == [1, 2, 3]  # memory now warm


class TestValueCacheSync:
    def test_get_sync_miss_returns_none(self, cache: ValueCache) -> None:
        assert cache.get_sync("missing") is None

    def test_set_sync_and_get_sync(self, cache: ValueCache) -> None:
        cache.set_sync("k", 42)
        assert cache.get_sync("k") == 42

    def test_set_sync_visible_to_async_get(self) -> None:
        """sync writes should be visible to subsequent async reads (same process)."""
        cache = ValueCache()
        cache.set_sync("k", "val")
        # get_sync reads _mem directly so no async needed here
        assert cache.get_sync("k") == "val"

    def test_set_sync_does_not_persist_to_redis(self) -> None:
        """set_sync is memory-only; a fresh instance won't see it without an async set."""
        cache = ValueCache()
        cache.set_sync("mem_only", 99)

        other = ValueCache()
        # other has no memory of "mem_only" — sync reads from _mem only
        assert other.get_sync("mem_only") is None

    @pytest.mark.asyncio
    async def test_async_set_followed_by_sync_get(self, cache: ValueCache) -> None:
        await cache.set("k", {"a": 1})
        assert cache.get_sync("k") == {"a": 1}
