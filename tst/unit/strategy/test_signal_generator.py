"""Tests for strategy/signal_generator.py — SignalGenerator"""

from __future__ import annotations

import datetime as dt
from datetime import UTC, datetime

import pytest
from quantindicators.polars_store import PolarsStore
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from trading_strategy_sdk.factory import create_strategy

from trading.app.database import build_session_factory, init_db
from trading.core.clock import SYSTEM_CLOCK, SimulatedClock
from trading.core.schemas import CandleEvent, InstrumentType, SignalEvent
from trading.storage.cache import CacherFactory, ValueCache
from trading.strategy.service.generator import AlgoInstance, AlgoRunConfig, SignalGenerator
from trading.strategy.storage.store import ChartStore, ConfigStore
from trading.tick_ingest.storage.store import AuditStore

BASE_TIME = datetime(2025, 1, 6, 9, 15, tzinfo=UTC)


def _make_factory() -> CacherFactory:
    return CacherFactory(ValueCache(), SYSTEM_CLOCK)


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


def _build_algos(
    instrument_strategy_map: dict[str, str],
    instrument_types: dict[str, str],
) -> dict[str, AlgoInstance]:
    return {
        symbol: AlgoInstance(
            strategy=create_strategy(strategy_id),
            instrument_type=InstrumentType(
                instrument_types.get(symbol, InstrumentType.EQUITY.value)
            ),
        )
        for symbol, strategy_id in instrument_strategy_map.items()
    }


def make_registry(
    engine: AsyncEngine, warmup_candles: int = 5, algo_name: str = "test_algo"
) -> SignalGenerator:
    sf = build_session_factory(engine)
    instrument_strategy_map = {"INFY": "ema_crossover"}
    instrument_types = {"INFY": "EQUITY"}
    config = AlgoRunConfig(
        instrument_strategy_map=instrument_strategy_map,
        instrument_types=instrument_types,
        equity=100_000.0,
        warmup_candles=warmup_candles,
        algo_name=algo_name,
    )
    algos = _build_algos(instrument_strategy_map, instrument_types)
    store = PolarsStore()
    reg = SignalGenerator(
        config=config,
        chart=ChartStore(sf),
        config_store=ConfigStore(sf),
        audit=AuditStore(sf),
        factory=_make_factory(),
        algos=algos,
        store=store,
    )
    reg.setup()  # initialize strategies before any handle() calls
    return reg


def make_candle(symbol: str = "INFY", tick_log_id: int = 0, **overrides) -> CandleEvent:
    base = dict(
        symbol=symbol,
        instrument_type=InstrumentType.EQUITY,
        interval="1min",
        open=100.0,
        high=105.0,
        low=99.0,
        close=103.0,
        volume=1000,
        timestamp=BASE_TIME,
        tick_log_id=tick_log_id,
    )
    return CandleEvent(**{**base, **overrides})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_registry_builds_algo_instances(engine: AsyncEngine) -> None:
    reg = make_registry(engine)
    assert "INFY" in reg._algos


def test_registry_with_multiple_instruments(engine: AsyncEngine) -> None:
    sf = build_session_factory(engine)
    instrument_strategy_map = {"INFY": "ema_crossover", "TCS": "ema_crossover"}
    instrument_types = {"INFY": "EQUITY", "TCS": "EQUITY"}
    config = AlgoRunConfig(
        instrument_strategy_map=instrument_strategy_map,
        instrument_types=instrument_types,
    )
    algos = _build_algos(instrument_strategy_map, instrument_types)
    reg = SignalGenerator(
        config=config,
        chart=ChartStore(sf),
        config_store=ConfigStore(sf),
        audit=AuditStore(sf),
        factory=_make_factory(),
        algos=algos,
    )
    reg.setup()
    assert "INFY" in reg._algos
    assert "TCS" in reg._algos


# ---------------------------------------------------------------------------
# handle — unknown symbol
# ---------------------------------------------------------------------------


async def test_handle_unknown_symbol_returns_empty(engine: AsyncEngine) -> None:
    reg = make_registry(engine)
    result = await reg.handle(make_candle(symbol="UNKNOWN"))
    assert result == []


# ---------------------------------------------------------------------------
# handle — no signal (strategy hasn't warmed up yet)
# ---------------------------------------------------------------------------


async def test_handle_returns_empty_when_no_signal(engine: AsyncEngine) -> None:
    reg = make_registry(engine)
    result = await reg.handle(make_candle())
    assert isinstance(result, list)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# handle — bars_seen increments
# ---------------------------------------------------------------------------


async def test_bars_seen_increments_per_candle(engine: AsyncEngine) -> None:
    reg = make_registry(engine)
    await reg.handle(make_candle())
    await reg.handle(make_candle())
    assert reg._algos["INFY"].bars_seen == 2


# ---------------------------------------------------------------------------
# symbols_needing_rewarm / rewarm (trading-platform#40)
# ---------------------------------------------------------------------------


def test_symbols_needing_rewarm_before_any_tick(engine: AsyncEngine) -> None:
    reg = make_registry(engine)
    assert reg.symbols_needing_rewarm() == {"INFY"}


async def test_symbols_needing_rewarm_excludes_live_touched_symbol(engine: AsyncEngine) -> None:
    reg = make_registry(engine)
    await reg.handle(make_candle())
    assert reg.symbols_needing_rewarm() == set()


def test_rewarm_calls_strategy_warmup_for_untouched_symbol(engine: AsyncEngine) -> None:
    reg = make_registry(engine)
    calls: list[tuple[str, list]] = []
    reg._algos["INFY"].strategy.warmup = lambda symbol, candles: calls.append((symbol, candles))

    candles = [make_candle()]
    reg.rewarm({"INFY": candles})

    assert calls == [("INFY", candles)]


async def test_rewarm_skips_symbol_already_touched_by_live_tick(engine: AsyncEngine) -> None:
    reg = make_registry(engine)
    await reg.handle(make_candle())  # bars_seen -> 1

    calls: list[tuple[str, list]] = []
    reg._algos["INFY"].strategy.warmup = lambda symbol, candles: calls.append((symbol, candles))

    reg.rewarm({"INFY": [make_candle()]})

    assert calls == []


def test_rewarm_ignores_symbol_not_configured_for_this_generator(engine: AsyncEngine) -> None:
    reg = make_registry(engine)
    # Must not raise for a symbol this SignalGenerator has no AlgoInstance for.
    reg.rewarm({"UNKNOWN": [make_candle(symbol="UNKNOWN")]})


# ---------------------------------------------------------------------------
# handle — produces SignalEvent when strategy fires
# ---------------------------------------------------------------------------


async def test_handle_returns_signal_when_crossover(engine: AsyncEngine) -> None:
    reg = make_registry(engine, warmup_candles=5, algo_name="test_algo")

    prices = [200.0 - i for i in range(30)] + [170.0 + i * 2 for i in range(30)]
    signals = []
    for i, price in enumerate(prices):
        candle = make_candle(
            open=price,
            high=price + 1,
            low=price - 1,
            close=price,
            timestamp=BASE_TIME + dt.timedelta(minutes=i),
        )
        result = await reg.handle(candle)
        signals.extend(result)

    assert len(signals) >= 1
    assert isinstance(signals[0], SignalEvent)
    assert signals[0].symbol == "INFY"


# ---------------------------------------------------------------------------
# handle — multi-instrument fan-out
# ---------------------------------------------------------------------------


async def test_handle_only_affects_matching_symbol(engine: AsyncEngine) -> None:
    sf = build_session_factory(engine)
    instrument_strategy_map = {"INFY": "ema_crossover", "TCS": "ema_crossover"}
    instrument_types = {"INFY": "EQUITY", "TCS": "EQUITY"}
    config = AlgoRunConfig(
        instrument_strategy_map=instrument_strategy_map,
        instrument_types=instrument_types,
        warmup_candles=5,
    )
    algos = _build_algos(instrument_strategy_map, instrument_types)
    reg = SignalGenerator(
        config=config,
        chart=ChartStore(sf),
        config_store=ConfigStore(sf),
        audit=AuditStore(sf),
        factory=_make_factory(),
        algos=algos,
    )
    reg.setup()

    await reg.handle(make_candle(symbol="INFY"))

    assert reg._algos["INFY"].bars_seen == 1
    assert reg._algos["TCS"].bars_seen == 0


# ---------------------------------------------------------------------------
# handle — tick_log_id != 0 triggers upsert
# ---------------------------------------------------------------------------


async def test_handle_upsert_triggered_when_tick_log_id_nonzero(engine: AsyncEngine) -> None:
    reg = make_registry(engine)
    await reg.handle(make_candle(tick_log_id=1))
    assert reg._algos["INFY"].bars_seen == 1


# ---------------------------------------------------------------------------
# handle — signal path with tick_log_id != 0
# ---------------------------------------------------------------------------


async def test_handle_signal_with_nonzero_tick_log_id(engine: AsyncEngine) -> None:
    reg = make_registry(engine, warmup_candles=5, algo_name="audit_test")

    prices = [200.0 - i for i in range(30)] + [170.0 + i * 2 for i in range(30)]
    signals = []
    for i, price in enumerate(prices):
        candle = make_candle(
            open=price,
            high=price + 1,
            low=price - 1,
            close=price,
            timestamp=BASE_TIME + dt.timedelta(minutes=i),
            tick_log_id=i + 1,
        )
        result = await reg.handle(candle)
        signals.extend(result)

    assert len(signals) >= 1
    assert isinstance(signals[0], SignalEvent)


# ---------------------------------------------------------------------------
# _upsert_state and _log_signal — direct coverage
# ---------------------------------------------------------------------------


async def test_upsert_state_direct(engine: AsyncEngine) -> None:
    reg = make_registry(engine)
    instance = reg._algos["INFY"]
    instance.bars_seen = 5
    await reg._upsert_state(instance)


async def test_log_signal_skips_when_tick_log_id_zero(engine: AsyncEngine) -> None:
    from trading.core.schemas import Side, SignalType

    reg = make_registry(engine)
    signal_event = SignalEvent(
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        strategy_id="ema_crossover",
        signal_type=SignalType.ENTRY,
        stop_distance=10.0,
        tick_log_id=0,
    )
    await reg._log_signal(signal_event, "test")


async def test_log_signal_with_nonzero_tick_log_id(engine: AsyncEngine) -> None:
    from trading.core.schemas import Side, SignalType

    reg = make_registry(engine)
    signal_event = SignalEvent(
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        strategy_id="ema_crossover",
        signal_type=SignalType.ENTRY,
        stop_distance=10.0,
        tick_log_id=42,
    )
    await reg._log_signal(signal_event, "test_algo")


async def test_log_signal_audit_failure_is_swallowed() -> None:
    """Covers line 207: _log_signal exception handler when audit.log_decision raises."""
    from unittest.mock import AsyncMock

    from trading.core.schemas import Side, SignalType
    from trading.strategy.api.interfaces import AbstractAuditStore

    class _FailingAuditStore(AbstractAuditStore):
        async def log_tick(self, event, symbol):
            return 1

        async def log_decision(self, **kwargs):
            raise RuntimeError("audit DB down in _log_signal")

        async def log_audit(self, module, level, message):
            pass

    mock_chart = AsyncMock()
    mock_config_store = AsyncMock()

    from quantindicators.polars_store import PolarsStore

    from trading.strategy.service.generator import AlgoRunConfig, SignalGenerator

    config = AlgoRunConfig(
        instrument_strategy_map={"INFY": "ema_crossover"},
        algo_name="fail_audit_test",
    )
    reg = SignalGenerator(
        config=config,
        chart=mock_chart,
        config_store=mock_config_store,
        audit=_FailingAuditStore(),
        factory=_make_factory(),
        algos={},
        store=PolarsStore(),
    )

    signal_event = SignalEvent(
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        strategy_id="ema_crossover",
        signal_type=SignalType.ENTRY,
        stop_distance=10.0,
        tick_log_id=99,
    )
    # Should not raise — exception is logged and swallowed
    await reg._log_signal(signal_event, "fail_audit_test")


# ---------------------------------------------------------------------------
# set_indicator_store — covers the store override method
# ---------------------------------------------------------------------------


def test_set_indicator_store_replaces_store(engine: AsyncEngine) -> None:
    """Covers set_indicator_store(): replaces the indicator store."""
    from quantindicators.polars_store import PolarsStore

    reg = make_registry(engine)
    new_store = PolarsStore()
    reg.set_indicator_store(new_store)
    assert reg._indicator_store is new_store


# ---------------------------------------------------------------------------
# AlgoConfig defaults
# ---------------------------------------------------------------------------


def test_algo_config_defaults() -> None:
    cfg = AlgoRunConfig(
        instrument_strategy_map={"INFY": "ema_crossover"},
    )
    assert cfg.equity == 100_000.0
    assert cfg.warmup_candles == 200
    assert cfg.algo_name == "default"


# ---------------------------------------------------------------------------
# setup() and warmup() lifecycle
# ---------------------------------------------------------------------------


async def test_handle_without_setup_returns_empty(engine: AsyncEngine) -> None:
    """handle() before setup() returns [] and logs a warning — no crash."""
    sf = build_session_factory(engine)
    instrument_strategy_map = {"INFY": "ema_crossover"}
    instrument_types = {"INFY": "EQUITY"}
    config = AlgoRunConfig(
        instrument_strategy_map=instrument_strategy_map,
        instrument_types=instrument_types,
    )
    algos = _build_algos(instrument_strategy_map, instrument_types)
    reg = SignalGenerator(
        config=config,
        chart=ChartStore(sf),
        config_store=ConfigStore(sf),
        audit=AuditStore(sf),
        factory=_make_factory(),
        algos=algos,
    )
    # setup() intentionally NOT called
    result = await reg.handle(make_candle())
    assert result == []


# ---------------------------------------------------------------------------
# AlgoInstance — pure unit tests (no DB, no collaborators)
# ---------------------------------------------------------------------------


def _make_instance() -> AlgoInstance:
    return AlgoInstance(
        strategy=create_strategy("ema_crossover"),
        instrument_type=InstrumentType.EQUITY,
    )


def test_algo_instance_tick_bar_increments_bars_seen() -> None:
    inst = _make_instance()
    inst.tick_bar("1min", warmup_candles=5)
    assert inst.bars_seen == 1


def test_algo_instance_tick_bar_updates_interval() -> None:
    inst = _make_instance()
    inst.tick_bar("5min", warmup_candles=5)
    assert inst.interval == "5min"


def test_algo_instance_tick_bar_sets_warmup_flag_at_threshold() -> None:
    inst = _make_instance()
    for _ in range(5):
        inst.tick_bar("1min", warmup_candles=5)
    assert inst.warmed_up is True


def test_algo_instance_tick_bar_no_warmup_before_threshold() -> None:
    inst = _make_instance()
    inst.tick_bar("1min", warmup_candles=5)
    assert inst.warmed_up is False


def test_algo_instance_record_signal_sets_timestamp() -> None:
    inst = _make_instance()
    now = datetime(2025, 1, 6, 9, 30, tzinfo=UTC)
    inst.record_signal(now)
    assert inst.last_signal_at == now.isoformat()


def test_algo_instance_is_ready_false_before_setup() -> None:
    inst = _make_instance()
    assert inst.is_ready() is False


def test_algo_instance_is_ready_true_after_setup() -> None:
    from quantindicators.polars_store import PolarsStore

    inst = _make_instance()
    inst.strategy.set_store(PolarsStore())
    assert inst.is_ready() is True


def test_algo_instance_state_dict_before_warmup() -> None:
    inst = _make_instance()
    d = inst.state_dict(warmup_candles=10)
    assert d["bars_seen"] == 0
    assert d["warmup_complete"] is False
    assert d["bars_remaining"] == 10
    assert d["last_signal_at"] is None


def test_algo_instance_state_dict_after_warmup() -> None:
    inst = _make_instance()
    for _ in range(10):
        inst.tick_bar("1min", warmup_candles=10)
    d = inst.state_dict(warmup_candles=10)
    assert d["warmup_complete"] is True
    assert d["bars_remaining"] == 0
    assert d["bars_seen"] == 10


def test_algo_instance_state_dict_bars_remaining_clamped() -> None:
    inst = _make_instance()
    inst.tick_bar("1min", warmup_candles=3)
    d = inst.state_dict(warmup_candles=3)
    assert d["bars_remaining"] == 2


def test_warmup_pre_builds_ema_crossover_indicators(engine: AsyncEngine) -> None:
    """After setup(warmup_candles), EmaCrossoverStrategy has indicator instances ready."""
    reg = make_registry(engine)  # calls setup() with no warmup candles
    strategy = reg._algos["INFY"].strategy

    # _inds should be empty before any candles (no warmup data provided)
    assert strategy._inds == {}  # type: ignore[union-attr]

    # Provide a warmup candle — rebuild with warmup data
    sf = build_session_factory(engine)
    config = AlgoRunConfig(
        instrument_strategy_map={"INFY": "ema_crossover"},
        instrument_types={"INFY": "EQUITY"},
    )
    algos = _build_algos({"INFY": "ema_crossover"}, {"INFY": "EQUITY"})
    reg2 = SignalGenerator(
        config=config,
        chart=ChartStore(sf),
        config_store=ConfigStore(sf),
        audit=AuditStore(sf),
        factory=_make_factory(),
        algos=algos,
    )
    warmup = {"INFY": [make_candle()]}
    reg2.setup(warmup)
    # After setup() with a candle for INFY, indicators are pre-built
    assert "INFY" in reg2._algos["INFY"].strategy._inds  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# handle — interval filter (trading-platform#36)
# ---------------------------------------------------------------------------


def _make_registry_with_interval(engine: AsyncEngine, interval: str) -> SignalGenerator:
    sf = build_session_factory(engine)
    instrument_strategy_map = {"INFY": "ema_crossover"}
    instrument_types = {"INFY": "EQUITY"}
    config = AlgoRunConfig(
        instrument_strategy_map=instrument_strategy_map,
        instrument_types=instrument_types,
        equity=100_000.0,
        warmup_candles=5,
        algo_name="test_algo",
    )
    algos = _build_algos(instrument_strategy_map, instrument_types)
    reg = SignalGenerator(
        config=config,
        chart=ChartStore(sf),
        config_store=ConfigStore(sf),
        audit=AuditStore(sf),
        factory=_make_factory(),
        algos=algos,
        store=PolarsStore(),
        interval=interval,
    )
    reg.setup()
    return reg


async def test_handle_drops_candle_for_other_interval(engine: AsyncEngine) -> None:
    reg = _make_registry_with_interval(engine, "5min")
    result = await reg.handle(make_candle(interval="1min"))
    assert result == []
    assert reg._algos["INFY"].bars_seen == 0


async def test_handle_processes_candle_for_matching_interval(engine: AsyncEngine) -> None:
    reg = _make_registry_with_interval(engine, "5min")
    await reg.handle(make_candle(interval="5min"))
    assert reg._algos["INFY"].bars_seen == 1


async def test_handle_mixed_intervals_only_advances_on_match(engine: AsyncEngine) -> None:
    reg = _make_registry_with_interval(engine, "5min")
    await reg.handle(make_candle(interval="1min"))
    await reg.handle(make_candle(interval="5min"))
    await reg.handle(make_candle(interval="1min"))
    assert reg._algos["INFY"].bars_seen == 1


async def test_handle_still_pushes_other_interval_candles_to_shared_store(
    engine: AsyncEngine,
) -> None:
    """A dropped-interval candle must still reach the shared PolarsStore --
    other code (e.g. multi-timeframe indicators) may fetch() it directly."""
    sf = build_session_factory(engine)
    instrument_strategy_map = {"INFY": "ema_crossover"}
    instrument_types = {"INFY": "EQUITY"}
    config = AlgoRunConfig(
        instrument_strategy_map=instrument_strategy_map,
        instrument_types=instrument_types,
        equity=100_000.0,
        warmup_candles=5,
        algo_name="test_algo",
    )
    algos = _build_algos(instrument_strategy_map, instrument_types)
    store = PolarsStore()
    reg = SignalGenerator(
        config=config,
        chart=ChartStore(sf),
        config_store=ConfigStore(sf),
        audit=AuditStore(sf),
        factory=_make_factory(),
        algos=algos,
        store=store,
        interval="5min",
    )
    reg.setup()
    await reg.handle(make_candle(interval="1min"))
    rows = await store.fetch("INFY", "1min", limit=10)
    assert len(rows) == 1


async def test_handle_permissive_default_interval_processes_any_interval(
    engine: AsyncEngine,
) -> None:
    """No interval= passed -> "" default -> no filtering, existing callers unaffected."""
    reg = make_registry(engine)
    await reg.handle(make_candle(interval="1min"))
    await reg.handle(make_candle(interval="5min"))
    assert reg._algos["INFY"].bars_seen == 2


# ---------------------------------------------------------------------------
# restore_state (trading-platform#79)
# ---------------------------------------------------------------------------

RESTORE_NOW = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)


def _cached_state(symbol: str, fast: float, slow: float) -> dict[str, object]:
    return {
        "prev_fast": {symbol: fast},
        "prev_slow": {symbol: slow},
        "last_fast": fast,
        "last_slow": slow,
        "last_atr": 1.0,
        "last_close": 100.0,
    }


def _make_registry_for_restore(
    engine: AsyncEngine, interval: str, factory: CacherFactory, clock: SimulatedClock
) -> SignalGenerator:
    sf = build_session_factory(engine)
    instrument_strategy_map = {"INFY": "ema_crossover", "RELIANCE": "ema_crossover"}
    instrument_types = {"INFY": "EQUITY", "RELIANCE": "EQUITY"}
    config = AlgoRunConfig(
        instrument_strategy_map=instrument_strategy_map,
        instrument_types=instrument_types,
        equity=100_000.0,
        warmup_candles=5,
        algo_name="test_algo",
    )
    algos = _build_algos(instrument_strategy_map, instrument_types)
    reg = SignalGenerator(
        config=config,
        chart=ChartStore(sf),
        config_store=ConfigStore(sf),
        audit=AuditStore(sf),
        factory=factory,
        algos=algos,
        store=PolarsStore(),
        interval=interval,
        clock=clock,
    )
    reg.setup()
    return reg


class TestRestoreState:
    async def test_noop_when_interval_unset(self, engine: AsyncEngine) -> None:
        """make_registry()'s default interval="" (permissive/test construction,
        see SignalGenerator.__init__) means restore_state() must no-op --
        the cache key schema requires a real interval."""
        reg = make_registry(engine)
        await reg._factory.rolling_state().save(
            algo="ema_crossover",
            symbol="INFY",
            interval="5min",
            tick_log_id=1,
            data=_cached_state("INFY", 111.0, 222.0),
            saved_at=RESTORE_NOW,
        )

        await reg.restore_state()

        assert reg._algos["INFY"].strategy._prev_fast == {}  # type: ignore[union-attr]

    async def test_restores_valid_same_day_cached_state(self, engine: AsyncEngine) -> None:
        clock = SimulatedClock()
        clock.advance(RESTORE_NOW)
        factory = CacherFactory(ValueCache(), SYSTEM_CLOCK)
        reg = _make_registry_for_restore(engine, "5min", factory, clock)
        await factory.rolling_state().save(
            algo="ema_crossover",
            symbol="INFY",
            interval="5min",
            tick_log_id=42,
            data=_cached_state("INFY", 111.0, 222.0),
            saved_at=RESTORE_NOW,
        )

        await reg.restore_state()

        strat = reg._algos["INFY"].strategy
        assert strat._prev_fast == {"INFY": 111.0}  # type: ignore[union-attr]
        assert strat._prev_slow == {"INFY": 222.0}  # type: ignore[union-attr]
        assert strat._last_close == 100.0  # type: ignore[union-attr]
        # RELIANCE had no cache entry -- left on its post-warmup default, untouched
        assert reg._algos["RELIANCE"].strategy._prev_fast == {}  # type: ignore[union-attr]

    async def test_stale_cross_day_cached_state_is_not_restored(
        self, engine: AsyncEngine
    ) -> None:
        clock = SimulatedClock()
        clock.advance(RESTORE_NOW)
        factory = CacherFactory(ValueCache(), SYSTEM_CLOCK)
        reg = _make_registry_for_restore(engine, "5min", factory, clock)
        saved_at = RESTORE_NOW - dt.timedelta(days=1)
        await factory.rolling_state().save(
            algo="ema_crossover",
            symbol="INFY",
            interval="5min",
            tick_log_id=1,
            data=_cached_state("INFY", 111.0, 222.0),
            saved_at=saved_at,
        )

        await reg.restore_state()

        assert reg._algos["INFY"].strategy._prev_fast == {}  # type: ignore[union-attr]

    async def test_missing_cache_entry_leaves_warmup_state(self, engine: AsyncEngine) -> None:
        clock = SimulatedClock()
        clock.advance(RESTORE_NOW)
        factory = CacherFactory(ValueCache(), SYSTEM_CLOCK)
        reg = _make_registry_for_restore(engine, "5min", factory, clock)

        await reg.restore_state()

        assert reg._algos["INFY"].strategy._prev_fast == {}  # type: ignore[union-attr]
        assert reg._algos["RELIANCE"].strategy._prev_fast == {}  # type: ignore[union-attr]

    async def test_symbols_filter_restricts_restoration(self, engine: AsyncEngine) -> None:
        clock = SimulatedClock()
        clock.advance(RESTORE_NOW)
        factory = CacherFactory(ValueCache(), SYSTEM_CLOCK)
        reg = _make_registry_for_restore(engine, "5min", factory, clock)
        for symbol in ("INFY", "RELIANCE"):
            await factory.rolling_state().save(
                algo="ema_crossover",
                symbol=symbol,
                interval="5min",
                tick_log_id=1,
                data=_cached_state(symbol, 1.0, 2.0),
                saved_at=RESTORE_NOW,
            )

        await reg.restore_state(symbols={"INFY"})

        assert reg._algos["INFY"].strategy._prev_fast == {"INFY": 1.0}  # type: ignore[union-attr]
        # Excluded by the symbols filter -- must stay on its post-warmup default,
        # even though a valid cache entry exists for it too.
        assert reg._algos["RELIANCE"].strategy._prev_fast == {}  # type: ignore[union-attr]

    async def test_invalid_cached_state_shape_is_cleared_and_ignored(
        self, engine: AsyncEngine
    ) -> None:
        """restore_from_state() returns False on a malformed state dict (e.g. missing
        prev_fast) -- restore_state() must clear that cache entry rather than leave a
        permanently-unrestorable row, and leave warmup state standing for this call."""
        clock = SimulatedClock()
        clock.advance(RESTORE_NOW)
        factory = CacherFactory(ValueCache(), SYSTEM_CLOCK)
        reg = _make_registry_for_restore(engine, "5min", factory, clock)
        await factory.rolling_state().save(
            algo="ema_crossover",
            symbol="INFY",
            interval="5min",
            tick_log_id=1,
            data={"garbage": True},
            saved_at=RESTORE_NOW,
        )

        await reg.restore_state()

        assert reg._algos["INFY"].strategy._prev_fast == {}  # type: ignore[union-attr]
        assert (
            await factory.rolling_state().load_latest(
                "ema_crossover", "INFY", "5min", now=RESTORE_NOW
            )
            is None
        )
