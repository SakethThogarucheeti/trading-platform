"""Tests for PostgresKVCache (trading-platform#79)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.app.database import build_session_factory, init_db
from trading.storage.cache.postgres_backend import PostgresKVCache


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
def cache(engine: AsyncEngine) -> PostgresKVCache:
    return PostgresKVCache(build_session_factory(engine))


class TestPostgresKVCache:
    @pytest.mark.asyncio
    async def test_get_missing_key_returns_none(self, cache: PostgresKVCache) -> None:
        assert await cache.get("nope") is None

    @pytest.mark.asyncio
    async def test_set_then_get_round_trips(self, cache: PostgresKVCache) -> None:
        await cache.set("k1", {"a": 1, "b": [1, 2, 3]})
        assert await cache.get("k1") == {"a": 1, "b": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_set_overwrites_existing_key(self, cache: PostgresKVCache) -> None:
        await cache.set("k1", {"v": 1})
        await cache.set("k1", {"v": 2})
        assert await cache.get("k1") == {"v": 2}

    @pytest.mark.asyncio
    async def test_delete_removes_key(self, cache: PostgresKVCache) -> None:
        await cache.set("k1", {"v": 1})
        await cache.delete("k1")
        assert await cache.get("k1") is None

    @pytest.mark.asyncio
    async def test_delete_missing_key_does_not_raise(self, cache: PostgresKVCache) -> None:
        await cache.delete("never-existed")  # must not raise

    @pytest.mark.asyncio
    async def test_survives_a_new_cache_instance_same_session_factory(
        self, engine: AsyncEngine
    ) -> None:
        """The whole point of this backend: a value written by one instance
        (standing in for one process) is visible to a fresh instance backed
        by the same DB (standing in for that process restarting)."""
        sf = build_session_factory(engine)
        await PostgresKVCache(sf).set("k1", {"restored": True})

        fresh = PostgresKVCache(sf)
        assert await fresh.get("k1") == {"restored": True}

    @pytest.mark.asyncio
    async def test_keys_are_isolated(self, cache: PostgresKVCache) -> None:
        await cache.set("k1", {"v": 1})
        await cache.set("k2", {"v": 2})
        assert await cache.get("k1") == {"v": 1}
        assert await cache.get("k2") == {"v": 2}
