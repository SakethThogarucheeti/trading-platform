"""
ValueCache / ApiResponseCacher — real Redis regression tests.

Two bugs found in production, both only reachable with a genuine Redis
backend (the unit tests only ever exercised the in-memory cashews fallback
via setup_cache(None)):

1. ValueCache's in-memory tier never honored ttl — a value set with a short
   ttl stayed "fresh" in-process forever even after it expired in Redis, so
   a warm process could keep serving stale data indefinitely. Fixed by
   tracking an expiry alongside the in-memory entry and treating it as a
   miss once elapsed (backend.py: ValueCache._mem_get).

2. ApiResponseCacher.invalidate_pnl() called invalidate("pnl", date) — a
   2-arg key — but /api/pnl's real cache key is
   ("pnl", date, session_id, algo_name), a 4-arg key. The invalidation
   silently missed every real cached response, so a fresh fill never
   showed up in /api/pnl until the TTL expired on its own.

Kept small (a handful of keys, sub-second TTLs) so this stays fast.
"""

from __future__ import annotations

import asyncio

from trading.core.clock import SimulatedClock
from trading.storage.cache import CacherFactory, ValueCache, setup_cache


async def _cache(redis_url: str) -> ValueCache:
    setup_cache(redis_url)
    return ValueCache()


async def test_ttl_expiry_is_honored_by_the_in_memory_tier(redis_url):
    cache = await _cache(redis_url)

    await cache.set("k:ttl", {"v": 1}, ttl=1)
    assert await cache.get("k:ttl") == {"v": 1}
    assert cache.get_sync("k:ttl") == {"v": 1}

    await asyncio.sleep(1.2)

    # Real Redis has expired the key by now, and the in-memory tier must
    # not keep serving the stale value past its own recorded expiry.
    assert cache.get_sync("k:ttl") is None
    assert await cache.get("k:ttl") is None


async def test_cross_process_hit_mirrors_redis_ttl_not_forever(redis_url):
    """A value set by one ValueCache instance and read by a second (simulating
    two processes sharing Redis) must not be cached forever locally just
    because the second instance's memory tier was empty on first read."""
    writer = await _cache(redis_url)
    await writer.set("k:shared", {"v": 42}, ttl=1)

    reader = ValueCache()  # fresh in-memory tier, same configured Redis backend
    assert await reader.get("k:shared") == {"v": 42}

    await asyncio.sleep(1.2)

    assert await reader.get("k:shared") is None


async def test_set_sync_then_async_get_round_trips_through_redis(redis_url):
    cache = await _cache(redis_url)

    cache.set_sync("k:fill", {"qty": 3})
    assert cache.get_sync("k:fill") == {"qty": 3}

    # A later async get for a key with no expiry must not be treated as
    # a miss just because it originated from the sync path.
    assert await cache.get("k:fill") == {"qty": 3}


async def test_invalidate_pnl_hits_the_real_four_part_cache_key(redis_url):
    """The core regression: /api/pnl's actual cache key has 4 parts, so
    invalidate_pnl() must clear that exact key, not some other shape."""
    cache = await _cache(redis_url)
    clock = SimulatedClock()
    factory = CacherFactory(cache, clock)
    api = factory.api()

    from datetime import date

    today = date(2026, 8, 20)

    async def producer() -> str:
        return '{"pnl": 100}'

    cached = await api.get_or_set_response(("pnl", today.isoformat(), "", ""), producer, ttl=300)
    assert cached == '{"pnl": 100}'

    await api.invalidate_pnl(today)

    calls = {"n": 0}

    async def fresh_producer() -> str:
        calls["n"] += 1
        return '{"pnl": 250}'

    result = await api.get_or_set_response(
        ("pnl", today.isoformat(), "", ""), fresh_producer, ttl=300
    )
    assert result == '{"pnl": 250}'
    assert calls["n"] == 1, "invalidate_pnl must have actually cleared the real 4-part key"


async def test_invalidate_pnl_does_not_touch_unrelated_keys(redis_url):
    cache = await _cache(redis_url)
    clock = SimulatedClock()
    factory = CacherFactory(cache, clock)
    api = factory.api()

    from datetime import date

    today = date(2026, 8, 20)

    async def other_producer() -> str:
        return '{"unrelated": true}'

    await api.get_or_set_response(("other", "key"), other_producer, ttl=300)
    await api.invalidate_pnl(today)

    calls = {"n": 0}

    async def should_not_be_called() -> str:
        calls["n"] += 1
        return '{"unrelated": false}'

    result = await api.get_or_set_response(("other", "key"), should_not_be_called, ttl=300)
    assert result == '{"unrelated": true}'
    assert calls["n"] == 0
