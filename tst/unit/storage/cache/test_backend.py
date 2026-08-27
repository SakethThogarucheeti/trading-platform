"""Tests for ValueCache in-memory backend."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trading.storage.cache.backend import ValueCache


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
    async def test_ttl_expires_in_memory_entry(self, cache: ValueCache) -> None:
        with patch("trading.storage.cache.backend.time.monotonic", return_value=1000.0):
            await cache.set("k", {"x": 1}, ttl=30)
            assert await cache.get("k") == {"x": 1}

        with patch("trading.storage.cache.backend.time.monotonic", return_value=1000.0 + 31):
            assert await cache.get("k") is None

    @pytest.mark.asyncio
    async def test_ttl_not_yet_expired_still_served(self, cache: ValueCache) -> None:
        with patch("trading.storage.cache.backend.time.monotonic", return_value=1000.0):
            await cache.set("k", {"x": 1}, ttl=30)

        with patch("trading.storage.cache.backend.time.monotonic", return_value=1000.0 + 10):
            assert await cache.get("k") == {"x": 1}

    @pytest.mark.asyncio
    async def test_no_ttl_never_expires(self, cache: ValueCache) -> None:
        with patch("trading.storage.cache.backend.time.monotonic", return_value=1000.0):
            await cache.set("k", {"x": 1})  # ttl=None

        with patch("trading.storage.cache.backend.time.monotonic", return_value=1000.0 + 10_000):
            assert await cache.get("k") == {"x": 1}

    @pytest.mark.asyncio
    async def test_instances_do_not_share_memory(self, cache: ValueCache) -> None:
        """Each ValueCache instance is independent -- no shared backend to populate from."""
        await cache.set("shared", [1, 2, 3])

        other = ValueCache()
        assert other.get_sync("shared") is None
        assert await other.get("shared") is None


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

    def test_set_sync_does_not_persist_across_instances(self) -> None:
        """set_sync is memory-only; a fresh instance won't see it."""
        cache = ValueCache()
        cache.set_sync("mem_only", 99)

        other = ValueCache()
        assert other.get_sync("mem_only") is None

    @pytest.mark.asyncio
    async def test_async_set_followed_by_sync_get(self, cache: ValueCache) -> None:
        await cache.set("k", {"a": 1})
        assert cache.get_sync("k") == {"a": 1}
