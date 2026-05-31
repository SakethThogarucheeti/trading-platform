"""Opening Range Breakout (ORB) Strategy."""

from __future__ import annotations

import logging
import math
from datetime import date, time
from typing import TypedDict, cast

from quantindicators.library.atr import ATR
from quantindicators.store import AbstractCandleStore

from trading.core.clock import SYSTEM_CLOCK, Clock
from trading.core.schemas import CandleEvent, InstrumentType, Side, SignalType
from trading.strategy.base import RuntimeContext, Signal, Strategy


class _OrbEntry(TypedDict):
    """Serialized form of one symbol's ORB state (JSON-safe)."""
    session_date: str   # ISO date string
    or_high: float
    or_low: float
    signal_taken: bool


class _State(TypedDict, total=False):
    orb_state: dict[str, _OrbEntry]
    last_atr: float | None
    last_or_high: float | None
    last_or_low: float | None

logger = logging.getLogger(__name__)

_SESSION_OPEN = time(9, 15)


class OpeningRangeBreakoutStrategy(Strategy):
    """
    Trade the first breakout beyond the session's opening range.

    The first orb_bars × interval_minutes of each session define the
    Opening Range (OR). A BUY signal fires when close breaks above OR high;
    SELL when close breaks below OR low. One signal per session.
    Stop distance = ATR × atr_multiplier.
    """

    alias = "opening_range_breakout"

    def __init__(
        self,
        orb_bars: int = 4,
        atr_period: int = 14,
        atr_multiplier: float = 1.5,
        interval_minutes: int = 15,
        runtime_context: RuntimeContext | None = None,
    ) -> None:
        if orb_bars < 1:
            raise ValueError(f"orb_bars must be >= 1, got {orb_bars}")
        self._orb_bars = orb_bars
        self._atr_period = atr_period
        self._atr_multiplier = atr_multiplier
        self._interval_minutes = interval_minutes
        self._clock: Clock = SYSTEM_CLOCK
        if runtime_context is not None:
            self.set_runtime_context(runtime_context)
        self._store: AbstractCandleStore | None = None
        # indicator cache: symbol → atr
        self._inds: dict[str, ATR] = {}
        # (session_date, or_high, or_low, signal_taken)
        self._state: dict[str, tuple[object, float, float, bool]] = {}
        # last computed values for dashboard state
        self._last_atr: float | None = None
        self._last_or_high: float | None = None
        self._last_or_low: float | None = None

    def set_runtime_context(self, ctx: RuntimeContext) -> None:
        self._clock = ctx.clock

    def set_store(self, store: AbstractCandleStore) -> None:
        self._store = store

    def _get_atr(self, symbol: str, interval: str) -> ATR:
        if symbol not in self._inds:
            assert self._store is not None, "set_store() must be called before on_candle()"
            self._inds[symbol] = ATR(self._store, symbol, interval)
        return self._inds[symbol]

    def get_state(self) -> dict[str, object]:
        return {
            f"atr_{self._atr_period}": round(self._last_atr, 4)
            if self._last_atr is not None
            else None,
            "or_high": round(self._last_or_high, 2)
            if self._last_or_high is not None and not math.isinf(self._last_or_high)
            else None,
            "or_low": round(self._last_or_low, 2)
            if self._last_or_low is not None and not math.isinf(self._last_or_low)
            else None,
        }

    def rolling_state(self) -> dict[str, object]:
        orb_state: dict[str, _OrbEntry] = {
            sym: _OrbEntry(
                session_date=str(s[0]),
                or_high=s[1],
                or_low=s[2],
                signal_taken=s[3],
            )
            for sym, s in self._state.items()
        }
        return {
            "orb_state": orb_state,
            "last_atr": self._last_atr,
            "last_or_high": self._last_or_high,
            "last_or_low": self._last_or_low,
        }

    async def restore_from_state(self, state: dict[str, object]) -> bool:
        try:
            s = cast(_State, state)
            self._state = {
                sym: (
                    date.fromisoformat(e["session_date"]),
                    float(e["or_high"]),
                    float(e["or_low"]),
                    bool(e["signal_taken"]),
                )
                for sym, e in s["orb_state"].items()
            }
            self._last_atr = s.get("last_atr")
            self._last_or_high = s.get("last_or_high")
            self._last_or_low = s.get("last_or_low")
            return True
        except (KeyError, TypeError, AttributeError, ValueError):
            return False

    async def on_candle(
        self,
        symbol: str,
        instrument_type: InstrumentType,
        candle: CandleEvent,
    ) -> Signal | None:
        atr_ind = self._get_atr(symbol, candle.interval)
        atr = await atr_ind.compute(ATR.Parameters(period=self._atr_period))
        self._last_atr = atr
        self.chart("oscillators", f"atr_{self._atr_period}", atr, candle.timestamp)
        if atr is None or atr <= 0:
            return None

        ts_local = candle.timestamp.astimezone(self._clock.tz)
        cur_ist = ts_local.time()
        cur_date = ts_local.date()

        session_open_min = _SESSION_OPEN.hour * 60 + _SESSION_OPEN.minute
        or_end_min = session_open_min + self._orb_bars * self._interval_minutes
        or_end_ist = time(or_end_min // 60, or_end_min % 60)

        state = self._state.get(symbol)
        if state is None or state[0] != cur_date:
            self._state[symbol] = (cur_date, -math.inf, math.inf, False)
            state = self._state[symbol]

        _, or_high, or_low, signal_taken = state

        if cur_ist < or_end_ist:
            new_high = max(or_high, candle.high)
            new_low = min(or_low, candle.low)
            self._state[symbol] = (cur_date, new_high, new_low, False)
            self._last_or_high = float(new_high)
            self._last_or_low = float(new_low)
            return None

        if signal_taken or or_high == -math.inf or or_low == math.inf:
            return None

        stop_distance = self._atr_multiplier * atr

        if candle.close > or_high:
            self._state[symbol] = (cur_date, or_high, or_low, True)
            logger.info(
                "ORB[%s]: BUY  close=%.2f > OR_high=%.2f stop=%.4f",
                symbol,
                candle.close,
                or_high,
                stop_distance,
            )
            return Signal(
                symbol=symbol,
                instrument_type=instrument_type,
                side=Side.BUY,
                strategy_id=self.id,
                signal_type=SignalType.ENTRY,
                stop_distance=stop_distance,
                entry_price=candle.close,
                timestamp=candle.timestamp,
            )

        if candle.close < or_low:
            self._state[symbol] = (cur_date, or_high, or_low, True)
            logger.info(
                "ORB[%s]: SELL close=%.2f < OR_low=%.2f stop=%.4f",
                symbol,
                candle.close,
                or_low,
                stop_distance,
            )
            return Signal(
                symbol=symbol,
                instrument_type=instrument_type,
                side=Side.SELL,
                strategy_id=self.id,
                signal_type=SignalType.ENTRY,
                stop_distance=stop_distance,
                entry_price=candle.close,
                timestamp=candle.timestamp,
            )

        return None
