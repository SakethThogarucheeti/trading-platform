"""Tests for all strategy implementations (indicator-based API)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading.core.clock import SimulatedClock
from trading.core.schemas import CandleEvent, InstrumentType, Side
from quantindicators.polars_store import PolarsStore
from trading.strategy.base import RuntimeContext, Strategy
from trading.strategy.ema_crossover import EmaCrossoverStrategy
from trading.strategy.opening_range_breakout import OpeningRangeBreakoutStrategy
from trading.strategy.rsi_mean_reversion import RsiMeanReversionStrategy
from trading.strategy.vwap_reversion import VwapReversionStrategy

EQUITY = InstrumentType.EQUITY
BASE_TS = datetime(2025, 1, 6, 4, 15, tzinfo=UTC)  # 09:45 IST


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candle(
    close: float,
    offset_minutes: int = 0,
    *,
    symbol: str = "INFY",
    interval: str = "15min",
    open: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: int = 1000,
    ts: datetime | None = None,
) -> CandleEvent:
    o = open or close
    h = high or close + 1.0
    lo = low or close - 1.0
    t = ts or (BASE_TS + timedelta(minutes=offset_minutes))
    return CandleEvent(
        symbol=symbol,
        instrument_type=EQUITY,
        interval=interval,
        open=o,
        high=h,
        low=lo,
        close=close,
        volume=volume,
        timestamp=t,
        tick_log_id=0,
    )


class _Harness:
    """Feed CandleEvents through a strategy with a PolarsStore, like AlgoRegistry does."""

    def __init__(
        self,
        strategy: Strategy,
        symbol: str = "INFY",
        interval: str = "15min",
        clock: SimulatedClock | None = None,
    ) -> None:
        self._strategy = strategy
        self._symbol = symbol
        self._interval = interval
        self._store = PolarsStore()
        self._clock = clock
        strategy.set_store(self._store)

    async def feed(self, candle: CandleEvent):
        if self._clock is not None:
            self._clock.advance(candle.timestamp)
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


# ---------------------------------------------------------------------------
# RsiMeanReversionStrategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rsi_buy_signal_on_oversold_cross() -> None:
    strat = RsiMeanReversionStrategy()
    h = _Harness(strat)
    # Falling phase → RSI near 0
    for i in range(30):
        await h.feed(_candle(100.0 - i * 1.5, offset_minutes=i))
    # Rising phase → RSI crosses above 30
    signals = []
    for i in range(30):
        sig = await h.feed(_candle(55.0 + i * 1.0, offset_minutes=30 + i))
        if sig is not None:
            signals.append(sig)
    assert any(s.side == Side.BUY for s in signals), "Expected a BUY signal on oversold cross"


@pytest.mark.asyncio
async def test_rsi_sell_signal_on_overbought_cross() -> None:
    strat = RsiMeanReversionStrategy()
    h = _Harness(strat)
    # Rising phase → RSI above 70
    for i in range(30):
        await h.feed(_candle(100.0 + i * 1.5, offset_minutes=i))
    # Falling phase → RSI crosses below 70
    signals = []
    for i in range(30):
        sig = await h.feed(_candle(145.0 - i * 1.0, offset_minutes=30 + i))
        if sig is not None:
            signals.append(sig)
    assert any(s.side == Side.SELL for s in signals), "Expected a SELL signal on overbought cross"


@pytest.mark.asyncio
async def test_rsi_no_signal_on_insufficient_data() -> None:
    strat = RsiMeanReversionStrategy()
    h = _Harness(strat)
    sig = await h.feed(_candle(100.0))
    assert sig is None


@pytest.mark.asyncio
async def test_rsi_invalid_params_raise() -> None:
    with pytest.raises(ValueError):
        RsiMeanReversionStrategy(oversold=70.0, overbought=30.0)


def test_rsi_strategy_id() -> None:
    assert RsiMeanReversionStrategy().id == "rsi_mean_reversion"


# ---------------------------------------------------------------------------
# VwapReversionStrategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vwap_buy_signal() -> None:
    # SimulatedClock advances to each candle's timestamp so VWAP session
    # boundary is derived from the bar's date, not the wall clock.
    clock = SimulatedClock()
    strat = VwapReversionStrategy(vwap_band=0.5, runtime_context=RuntimeContext(clock=clock))
    h = _Harness(strat, clock=clock)

    session_open = datetime(2025, 1, 6, 3, 45, tzinfo=UTC)  # 09:15 IST

    # Anchor VWAP high with a heavy-volume bar
    await h.feed(_candle(120.0, ts=session_open, volume=100_000, open=120.0, high=121.0, low=119.0))

    signals = []
    # Feed bars far below VWAP (deviation >> vwap_band × ATR)
    for i in range(1, 15):
        price = 80.0 - i
        sig = await h.feed(
            _candle(
                price,
                ts=session_open + timedelta(minutes=i * 15),
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                volume=1000,
            )
        )
        if sig is not None:
            signals.append(sig)

    # Reversal bar: close higher than previous (momentum turning up) → BUY
    sig = await h.feed(
        _candle(
            70.0,
            ts=session_open + timedelta(minutes=15 * 15),
            open=65.0,
            high=70.5,
            low=64.5,
            volume=1000,
        )
    )
    if sig is not None:
        signals.append(sig)

    assert any(s.side == Side.BUY for s in signals), "Expected a BUY signal below VWAP"


@pytest.mark.asyncio
async def test_vwap_no_signal_on_insufficient_data() -> None:
    strat = VwapReversionStrategy()
    h = _Harness(strat)
    sig = await h.feed(_candle(100.0))
    assert sig is None


@pytest.mark.asyncio
async def test_vwap_invalid_band_raises() -> None:
    with pytest.raises(ValueError):
        VwapReversionStrategy(vwap_band=0.0)


def test_vwap_strategy_id() -> None:
    assert VwapReversionStrategy().id == "vwap_reversion"


# ---------------------------------------------------------------------------
# OpeningRangeBreakoutStrategy
# ---------------------------------------------------------------------------

_SESSION_OPEN_UTC = datetime(2025, 1, 6, 3, 45, tzinfo=UTC)  # 09:15 IST
_POST_ORB_UTC = datetime(2025, 1, 6, 4, 16, tzinfo=UTC)  # 09:46 IST (past 2×15min ORB)

# Use atr_period=3 so ATR warms up on 9 bars (3×3). Sessions are spaced by a day
# so each day's test data starts a fresh session.
_ORB_ATR_PERIOD = 3


async def _feed_warmup(h: _Harness, n: int = 10) -> None:
    """Feed n prior-day bars so ATR has enough data."""
    warmup_start = datetime(2025, 1, 5, 3, 45, tzinfo=UTC)  # previous day session open
    for i in range(n):
        await h.feed(
            _candle(100.0, ts=warmup_start + timedelta(minutes=i * 15), high=105.0, low=95.0)
        )


@pytest.mark.asyncio
async def test_orb_buy_breakout_above_range() -> None:
    clock = SimulatedClock()
    strat = OpeningRangeBreakoutStrategy(orb_bars=2, atr_period=_ORB_ATR_PERIOD, interval_minutes=15, runtime_context=RuntimeContext(clock=clock))
    h = _Harness(strat, clock=clock)
    await _feed_warmup(h)
    # 2 range bars establishing OR high=105, OR low=99
    for i in range(2):
        await h.feed(
            _candle(102.0, ts=_SESSION_OPEN_UTC + timedelta(minutes=i * 15), high=105.0, low=99.0)
        )
    # Post-ORB breakout: close above OR high
    sig = await h.feed(_candle(110.0, ts=_POST_ORB_UTC, high=111.0, low=104.0))
    assert sig is not None
    assert sig.side == Side.BUY


@pytest.mark.asyncio
async def test_orb_sell_breakout_below_range() -> None:
    clock = SimulatedClock()
    strat = OpeningRangeBreakoutStrategy(orb_bars=2, atr_period=_ORB_ATR_PERIOD, interval_minutes=15, runtime_context=RuntimeContext(clock=clock))
    h = _Harness(strat, clock=clock)
    await _feed_warmup(h)
    for i in range(2):
        await h.feed(
            _candle(102.0, ts=_SESSION_OPEN_UTC + timedelta(minutes=i * 15), high=105.0, low=99.0)
        )
    sig = await h.feed(_candle(94.0, ts=_POST_ORB_UTC, high=99.0, low=93.0))
    assert sig is not None
    assert sig.side == Side.SELL


@pytest.mark.asyncio
async def test_orb_only_one_signal_per_session() -> None:
    clock = SimulatedClock()
    strat = OpeningRangeBreakoutStrategy(orb_bars=2, atr_period=_ORB_ATR_PERIOD, interval_minutes=15, runtime_context=RuntimeContext(clock=clock))
    h = _Harness(strat, clock=clock)
    await _feed_warmup(h)
    for i in range(2):
        await h.feed(
            _candle(102.0, ts=_SESSION_OPEN_UTC + timedelta(minutes=i * 15), high=105.0, low=99.0)
        )
    sig1 = await h.feed(_candle(110.0, ts=_POST_ORB_UTC, high=111.0, low=104.0))
    assert sig1 is not None
    sig2 = await h.feed(
        _candle(114.0, ts=_POST_ORB_UTC + timedelta(minutes=15), high=115.0, low=108.0)
    )
    assert sig2 is None


@pytest.mark.asyncio
async def test_orb_no_signal_inside_orb_window() -> None:
    clock = SimulatedClock()
    strat = OpeningRangeBreakoutStrategy(orb_bars=4, atr_period=_ORB_ATR_PERIOD, interval_minutes=15, runtime_context=RuntimeContext(clock=clock))
    h = _Harness(strat, clock=clock)
    sig = await h.feed(_candle(110.0, ts=_SESSION_OPEN_UTC, high=115.0, low=99.0))
    assert sig is None


@pytest.mark.asyncio
async def test_orb_no_signal_on_insufficient_atr_data() -> None:
    clock = SimulatedClock()
    strat = OpeningRangeBreakoutStrategy(orb_bars=2, atr_period=_ORB_ATR_PERIOD, interval_minutes=15, runtime_context=RuntimeContext(clock=clock))
    h = _Harness(strat, clock=clock)
    sig = await h.feed(_candle(110.0, ts=_POST_ORB_UTC, high=111.0, low=109.0))
    assert sig is None


def test_orb_strategy_id() -> None:
    assert OpeningRangeBreakoutStrategy().id == "opening_range_breakout"


def test_orb_invalid_orb_bars_raises() -> None:
    with pytest.raises(ValueError):
        OpeningRangeBreakoutStrategy(orb_bars=0)


# ---------------------------------------------------------------------------
# EmaCrossoverStrategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ema_buy_signal_on_golden_cross() -> None:
    strat = EmaCrossoverStrategy(fast=3, slow=7)
    h = _Harness(strat)
    for i in range(20):
        await h.feed(_candle(100.0 - i, offset_minutes=i))
    signals = []
    for i in range(20):
        sig = await h.feed(_candle(80.0 + i * 2, offset_minutes=20 + i))
        if sig is not None:
            signals.append(sig)
    assert any(s.side == Side.BUY for s in signals), "Expected a BUY signal on golden cross"


@pytest.mark.asyncio
async def test_ema_sell_signal_on_death_cross() -> None:
    strat = EmaCrossoverStrategy(fast=3, slow=7)
    h = _Harness(strat)
    for i in range(20):
        await h.feed(_candle(100.0 + i, offset_minutes=i))
    signals = []
    for i in range(20):
        sig = await h.feed(_candle(120.0 - i * 2, offset_minutes=20 + i))
        if sig is not None:
            signals.append(sig)
    assert any(s.side == Side.SELL for s in signals), "Expected a SELL signal on death cross"


@pytest.mark.asyncio
async def test_ema_no_signal_on_insufficient_data() -> None:
    strat = EmaCrossoverStrategy()
    h = _Harness(strat)
    sig = await h.feed(_candle(100.0))
    assert sig is None


def test_ema_invalid_params_raise() -> None:
    with pytest.raises(ValueError):
        EmaCrossoverStrategy(fast=21, slow=9)


def test_ema_strategy_id() -> None:
    assert EmaCrossoverStrategy().id == "ema_crossover"


def test_ema_get_state_returns_dict() -> None:
    strat = EmaCrossoverStrategy()
    assert isinstance(strat.get_state(), dict)
