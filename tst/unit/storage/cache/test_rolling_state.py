"""Tests for RollingStateCacher."""

from __future__ import annotations

import pytest

from trading.storage.cache.backend import ValueCache, setup_cache, _backend
from trading.storage.cache.rolling_state import RollingStateCacher, _WINDOW_SIZE


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
def cacher(cache: ValueCache) -> RollingStateCacher:
    return RollingStateCacher(cache)


ALGO = "ema_crossover"
SYM = "RELIANCE"
INTERVAL = "5m"


class TestRollingStateCacherKeys:
    def test_make_key(self, cacher: RollingStateCacher) -> None:
        assert cacher.make_key("RELIANCE", "5m", 42) == "state:RELIANCE:5m:42"

    def test_win_key(self, cacher: RollingStateCacher) -> None:
        assert cacher._win_key("ema", "RELIANCE", "5m") == "win:ema:RELIANCE:5m"


class TestRollingStateCacherSaveAndLoad:
    @pytest.mark.asyncio
    async def test_load_latest_miss_returns_none(self, cacher: RollingStateCacher) -> None:
        result = await cacher.load_latest(ALGO, SYM, INTERVAL)
        assert result is None

    @pytest.mark.asyncio
    async def test_save_and_load_latest(self, cacher: RollingStateCacher) -> None:
        state = {"prev_fast": 1.5, "prev_slow": 1.2}
        await cacher.save(ALGO, SYM, INTERVAL, tick_log_id=100, data=state)
        result = await cacher.load_latest(ALGO, SYM, INTERVAL)
        assert result == (100, state)

    @pytest.mark.asyncio
    async def test_load_latest_returns_most_recent(self, cacher: RollingStateCacher) -> None:
        await cacher.save(ALGO, SYM, INTERVAL, 1, {"v": 1})
        await cacher.save(ALGO, SYM, INTERVAL, 2, {"v": 2})
        await cacher.save(ALGO, SYM, INTERVAL, 3, {"v": 3})
        tick_id, state = await cacher.load_latest(ALGO, SYM, INTERVAL)
        assert tick_id == 3
        assert state == {"v": 3}

    @pytest.mark.asyncio
    async def test_different_algos_are_isolated(self, cacher: RollingStateCacher) -> None:
        await cacher.save("algo_a", SYM, INTERVAL, 10, {"x": 1})
        await cacher.save("algo_b", SYM, INTERVAL, 20, {"x": 2})

        a = await cacher.load_latest("algo_a", SYM, INTERVAL)
        b = await cacher.load_latest("algo_b", SYM, INTERVAL)
        assert a == (10, {"x": 1})
        assert b == (20, {"x": 2})

    @pytest.mark.asyncio
    async def test_different_symbols_are_isolated(self, cacher: RollingStateCacher) -> None:
        await cacher.save(ALGO, "RELIANCE", INTERVAL, 10, {"x": 1})
        await cacher.save(ALGO, "INFY", INTERVAL, 20, {"x": 2})

        r = await cacher.load_latest(ALGO, "RELIANCE", INTERVAL)
        i = await cacher.load_latest(ALGO, "INFY", INTERVAL)
        assert r == (10, {"x": 1})
        assert i == (20, {"x": 2})


class TestRollingStateCacherWindow:
    @pytest.mark.asyncio
    async def test_window_trims_to_max_size(self, cacher: RollingStateCacher) -> None:
        for i in range(_WINDOW_SIZE + 10):
            await cacher.save(ALGO, SYM, INTERVAL, i, {"i": i})

        win_key = cacher._win_key(ALGO, SYM, INTERVAL)
        window = await cacher._cache.get(win_key)
        assert window is not None
        assert len(window) == _WINDOW_SIZE

    @pytest.mark.asyncio
    async def test_window_keeps_most_recent_ids(self, cacher: RollingStateCacher) -> None:
        n = _WINDOW_SIZE + 5
        for i in range(n):
            await cacher.save(ALGO, SYM, INTERVAL, i, {"i": i})

        win_key = cacher._win_key(ALGO, SYM, INTERVAL)
        window = await cacher._cache.get(win_key)
        expected_start = n - _WINDOW_SIZE
        assert window[0] == expected_start
        assert window[-1] == n - 1


class TestRollingStateCacherClear:
    @pytest.mark.asyncio
    async def test_clear_removes_window_and_state_entries(self, cacher: RollingStateCacher) -> None:
        for i in range(5):
            await cacher.save(ALGO, SYM, INTERVAL, i, {"i": i})

        await cacher.clear(ALGO, SYM, INTERVAL)
        assert await cacher.load_latest(ALGO, SYM, INTERVAL) is None

        for i in range(5):
            state = await cacher._cache.get(cacher.make_key(SYM, INTERVAL, i))
            assert state is None

    @pytest.mark.asyncio
    async def test_clear_on_empty_does_not_raise(self, cacher: RollingStateCacher) -> None:
        await cacher.clear(ALGO, SYM, INTERVAL)  # must not raise

    @pytest.mark.asyncio
    async def test_clear_does_not_affect_other_algos(self, cacher: RollingStateCacher) -> None:
        await cacher.save("algo_a", SYM, INTERVAL, 1, {"x": 1})
        await cacher.save("algo_b", SYM, INTERVAL, 2, {"x": 2})

        await cacher.clear("algo_a", SYM, INTERVAL)

        assert await cacher.load_latest("algo_a", SYM, INTERVAL) is None
        assert await cacher.load_latest("algo_b", SYM, INTERVAL) == (2, {"x": 2})
