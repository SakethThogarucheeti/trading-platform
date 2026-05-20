"""Tests for di/providers — factory functions: make_strategy"""

from __future__ import annotations

import pytest

from trading.di.providers.strategy import make_strategy
from trading.strategy.ema_crossover import EmaCrossoverStrategy
from trading.strategy.opening_range_breakout import OpeningRangeBreakoutStrategy
from trading.strategy.rsi_mean_reversion import RsiMeanReversionStrategy
from trading.strategy.vwap_reversion import VwapReversionStrategy


def test_make_strategy_ema_crossover() -> None:
    s = make_strategy("ema_crossover")
    assert isinstance(s, EmaCrossoverStrategy)


def test_make_strategy_rsi_mean_reversion() -> None:
    s = make_strategy("rsi_mean_reversion")
    assert isinstance(s, RsiMeanReversionStrategy)


def test_make_strategy_vwap_reversion() -> None:
    s = make_strategy("vwap_reversion")
    assert isinstance(s, VwapReversionStrategy)


def test_make_strategy_opening_range_breakout() -> None:
    s = make_strategy("opening_range_breakout")
    assert isinstance(s, OpeningRangeBreakoutStrategy)


def test_make_strategy_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown strategy"):
        make_strategy("nonexistent_strategy")


def test_make_strategy_passes_params() -> None:
    s = make_strategy("ema_crossover", params={"fast": 5, "slow": 13})
    assert isinstance(s, EmaCrossoverStrategy)


def test_make_strategy_injects_clock_when_accepted() -> None:
    from trading.core.clock import SimulatedClock

    clock = SimulatedClock()
    s = make_strategy("vwap_reversion", clock=clock)
    assert isinstance(s, VwapReversionStrategy)
    # Clock is stored on the strategy and forwarded to indicators at first candle
    assert s._clock is clock  # type: ignore[attr-defined]


def test_make_strategy_clock_ignored_when_not_accepted() -> None:
    from trading.core.clock import SimulatedClock

    clock = SimulatedClock()
    # EmaCrossoverStrategy does not accept clock — no TypeError should be raised
    s = make_strategy("ema_crossover", clock=clock)
    assert isinstance(s, EmaCrossoverStrategy)


def test_make_strategy_clock_none_no_injection() -> None:
    # clock=None → no injection path taken
    s = make_strategy("vwap_reversion", clock=None)
    assert isinstance(s, VwapReversionStrategy)
