"""Tests for strategy/base.py and EmaCrossoverStrategy (indicator-based API)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from trading.core.schemas import CandleEvent, InstrumentType, Side, SignalType
from quantindicators.polars_store import PolarsStore
from trading.strategy.ema_crossover import EmaCrossoverStrategy
from trading.strategy.base import Strategy

BASE_TIME = datetime(2025, 1, 6, 3, 45, 0, tzinfo=UTC)
INFY = "INFY"
EQUITY = InstrumentType.EQUITY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candle(
    close: float,
    offset_minutes: int,
    *,
    high: float | None = None,
    low: float | None = None,
) -> CandleEvent:
    return CandleEvent(
        symbol=INFY,
        instrument_type=EQUITY,
        interval="1min",
        open=close,
        high=high if high is not None else close + 1.0,
        low=low if low is not None else close - 1.0,
        close=close,
        volume=1000,
        timestamp=BASE_TIME + timedelta(minutes=offset_minutes),
        tick_log_id=0,
    )


class _Harness:
    def __init__(self, strategy: EmaCrossoverStrategy) -> None:
        self._strategy = strategy
        self._store = PolarsStore()
        strategy.set_store(self._store)

    async def feed(self, candle: CandleEvent):
        self._store.push(
            candle.symbol,
            candle.interval,
            {
                "symbol": candle.symbol,
                "interval": candle.interval,
                "ts": candle.timestamp,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            },
        )
        return await self._strategy.on_candle(candle.symbol, EQUITY, candle)


async def _feed_prices(strategy: EmaCrossoverStrategy, prices: list[float]) -> list:
    h = _Harness(strategy)
    signals = []
    for i, p in enumerate(prices):
        sig = await h.feed(_candle(p, offset_minutes=i))
        if sig is not None:
            signals.append(sig)
    return signals


# ---------------------------------------------------------------------------
# BUY crossover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_buy_signal_on_ema9_crossing_above_ema21() -> None:
    # fast=3, slow=7 so warmup is quicker in tests
    strat = EmaCrossoverStrategy(fast=3, slow=7)
    # Falling phase then sharp rise: fast crosses above slow
    prices = [100.0 - i for i in range(20)] + [80.0 + i * 2 for i in range(20)]
    signals = await _feed_prices(strat, prices)
    assert any(s.side == Side.BUY for s in signals)


@pytest.mark.asyncio
async def test_buy_signal_signal_type_is_entry() -> None:
    strat = EmaCrossoverStrategy(fast=3, slow=7)
    prices = [100.0 - i for i in range(20)] + [80.0 + i * 2 for i in range(20)]
    signals = await _feed_prices(strat, prices)
    buy_signals = [s for s in signals if s.side == Side.BUY]
    assert buy_signals
    assert all(s.signal_type == SignalType.ENTRY for s in buy_signals)
    assert all(s.symbol == INFY for s in buy_signals)
    assert all(s.strategy_id == "ema_crossover" for s in buy_signals)


@pytest.mark.asyncio
async def test_buy_signal_stop_distance_uses_atr_multiplier() -> None:
    strat = EmaCrossoverStrategy(fast=3, slow=7, atr_multiplier=2.0)
    prices = [100.0 - i for i in range(20)] + [80.0 + i * 2 for i in range(20)]
    signals = await _feed_prices(strat, prices)
    buy_signals = [s for s in signals if s.side == Side.BUY]
    assert buy_signals
    assert all(s.stop_distance > 0 for s in buy_signals)


# ---------------------------------------------------------------------------
# SELL crossover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sell_signal_on_ema9_crossing_below_ema21() -> None:
    strat = EmaCrossoverStrategy(fast=3, slow=7)
    prices = [100.0 + i for i in range(20)] + [120.0 - i * 2 for i in range(20)]
    signals = await _feed_prices(strat, prices)
    assert any(s.side == Side.SELL for s in signals)


# ---------------------------------------------------------------------------
# No signal cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_signal_on_single_bar() -> None:
    strat = EmaCrossoverStrategy()
    signals = await _feed_prices(strat, [100.0])
    assert signals == []


@pytest.mark.asyncio
async def test_no_signal_on_insufficient_data() -> None:
    strat = EmaCrossoverStrategy()
    # Only 3 bars — well below warmup threshold for EMA(9)/EMA(21)
    signals = await _feed_prices(strat, [100.0, 101.0, 102.0])
    assert signals == []


# ---------------------------------------------------------------------------
# Signal properties
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_signal_has_unique_signal_id() -> None:
    strat = EmaCrossoverStrategy(fast=3, slow=7)
    prices = [100.0 - i for i in range(20)] + [80.0 + i * 2 for i in range(20)]
    signals = await _feed_prices(strat, prices)
    ids = [s.signal_id for s in signals]
    assert len(ids) == len(set(ids)), "Each signal must have a unique signal_id"


@pytest.mark.asyncio
async def test_signal_id_is_valid_uuid() -> None:
    strat = EmaCrossoverStrategy(fast=3, slow=7)
    prices = [100.0 - i for i in range(20)] + [80.0 + i * 2 for i in range(20)]
    signals = await _feed_prices(strat, prices)
    assert signals
    assert isinstance(signals[0].signal_id, UUID)
    assert signals[0].signal_id.version == 4


@pytest.mark.asyncio
async def test_signal_stop_distance_always_positive() -> None:
    strat = EmaCrossoverStrategy(fast=3, slow=7)
    prices = [100.0 - i for i in range(20)] + [80.0 + i * 2 for i in range(20)]
    signals = await _feed_prices(strat, prices)
    assert signals
    assert all(s.stop_distance > 0 for s in signals)


# ---------------------------------------------------------------------------
# Strategy params validation
# ---------------------------------------------------------------------------


def test_invalid_params_raise() -> None:
    with pytest.raises(ValueError):
        EmaCrossoverStrategy(fast=21, slow=9)


def test_strategy_id() -> None:
    assert EmaCrossoverStrategy().id == "ema_crossover"


# ---------------------------------------------------------------------------
# Strategy base
# ---------------------------------------------------------------------------


def test_strategy_base_get_state_default() -> None:
    class _Minimal(Strategy):
        alias = "_test_minimal_strategy_xyz"

        async def on_candle(self, symbol, instrument_type, candle):
            return None

    strat = _Minimal()
    assert strat.get_state() == {}


def test_init_subclass_returns_early_when_no_alias() -> None:
    """Covers line 57: __init_subclass__ returns early when alias is not in class __dict__."""
    # Creating a subclass WITHOUT alias in its own __dict__ triggers the `return` at line 57
    class _NoAlias(Strategy):
        # No alias defined — should trigger the `return` at line 57

        async def on_candle(self, symbol, instrument_type, candle):
            return None

    # Successfully created — no error
    assert issubclass(_NoAlias, Strategy)


def test_init_subclass_raises_for_empty_alias() -> None:
    """Covers line 59: __init_subclass__ raises TypeError when alias is empty string."""
    with pytest.raises(TypeError, match="alias must be a non-empty string"):
        class _EmptyAlias(Strategy):
            alias = ""

            async def on_candle(self, symbol, instrument_type, candle):
                return None


def test_get_params_returns_empty_dict() -> None:
    """Covers line 97: get_params() returns {}."""
    class _MP(Strategy):
        alias = "_test_get_params_strategy"

        async def on_candle(self, symbol, instrument_type, candle):
            return None

    strat = _MP()
    assert strat.get_params() == {}


def test_set_chart_callback_stores_callback() -> None:
    """Covers set_chart_callback() stores the callback."""
    class _M(Strategy):
        alias = "_test_chart_cb_strategy"

        async def on_candle(self, symbol, instrument_type, candle):
            return None

    strat = _M()
    calls: list[tuple] = []

    def _cb(chart: str, series: str, value: float, ts) -> None:
        calls.append((chart, series, value))

    strat.set_chart_callback(_cb)
    assert strat._chart_cb is _cb


def test_chart_invokes_callback() -> None:
    """Covers line 59: chart() actually calls the stored callback."""
    from datetime import UTC, datetime

    class _M2(Strategy):
        alias = "_test_chart_invoke_strategy"

        async def on_candle(self, symbol, instrument_type, candle):
            return None

    strat = _M2()
    calls: list[tuple] = []

    def _cb(chart: str, series: str, value: float, ts) -> None:
        calls.append((chart, series, value, ts))

    strat.set_chart_callback(_cb)
    ts = datetime(2025, 1, 1, tzinfo=UTC)
    strat.chart("price", "ema", 1500.0, ts)
    assert len(calls) == 1
    assert calls[0] == ("price", "ema", 1500.0, ts)


def test_chart_uses_now_when_ts_is_none() -> None:
    """Covers line 97: chart() uses datetime.now(UTC) when ts is None."""
    from datetime import UTC, datetime

    class _M3(Strategy):
        alias = "_test_chart_now_strategy"

        async def on_candle(self, symbol, instrument_type, candle):
            return None

    strat = _M3()
    received_ts: list[datetime] = []

    def _cb(chart: str, series: str, value: float, ts) -> None:
        received_ts.append(ts)

    strat.set_chart_callback(_cb)
    before = datetime.now(UTC)
    strat.chart("price", "close", 100.0, None)  # ts=None → uses datetime.now(UTC)
    after = datetime.now(UTC)

    assert len(received_ts) == 1
    assert before <= received_ts[0] <= after
