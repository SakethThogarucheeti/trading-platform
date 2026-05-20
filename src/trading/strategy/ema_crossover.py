from __future__ import annotations

import logging

from trading.core.schemas import CandleEvent, InstrumentType, Side, SignalType
from quantindicators.library.atr import ATR
from quantindicators.library.ema import EMA
from quantindicators.store import AbstractCandleStore
from trading.strategy.base import Signal, Strategy

logger = logging.getLogger(__name__)

_DEFAULT_FAST = 9
_DEFAULT_SLOW = 21
_DEFAULT_ATR_PERIOD = 14
_DEFAULT_ATR_MULTIPLIER = 1.5


class EmaCrossoverStrategy(Strategy):
    """
    EMA crossover strategy.

    BUY  when fast EMA crosses above slow EMA.
    SELL when fast EMA crosses below slow EMA.
    Stop distance = atr_multiplier × ATR.
    """

    alias = "ema_crossover"

    def __init__(
        self,
        fast: int = _DEFAULT_FAST,
        slow: int = _DEFAULT_SLOW,
        atr_period: int = _DEFAULT_ATR_PERIOD,
        atr_multiplier: float = _DEFAULT_ATR_MULTIPLIER,
    ) -> None:
        if fast >= slow:
            raise ValueError(f"fast ({fast}) must be less than slow ({slow})")
        self._fast_period = fast
        self._slow_period = slow
        self._atr_period = atr_period
        self._atr_multiplier = atr_multiplier
        self._store: AbstractCandleStore | None = None
        # indicator cache: symbol → (fast_ema, slow_ema, atr)
        self._inds: dict[str, tuple[EMA, EMA, ATR]] = {}
        self._prev_fast: dict[str, float | None] = {}
        self._prev_slow: dict[str, float | None] = {}
        # last computed values for dashboard state
        self._last_fast: float | None = None
        self._last_slow: float | None = None
        self._last_atr: float | None = None
        self._last_close: float | None = None

    def set_store(self, store: AbstractCandleStore) -> None:
        self._store = store

    def _get_inds(self, symbol: str, interval: str) -> tuple[EMA, EMA, ATR]:
        if symbol not in self._inds:
            assert self._store is not None, "set_store() must be called before on_candle()"
            self._inds[symbol] = (
                EMA(self._store, symbol, interval),
                EMA(self._store, symbol, interval),
                ATR(self._store, symbol, interval),
            )
        return self._inds[symbol]

    def get_params(self) -> dict[str, object]:
        return {
            "fast": self._fast_period,
            "slow": self._slow_period,
            "atr_period": self._atr_period,
            "atr_multiplier": self._atr_multiplier,
        }

    def get_state(self) -> dict[str, object]:
        return {
            f"ema_{self._fast_period}": round(self._last_fast, 4)
            if self._last_fast is not None
            else None,
            f"ema_{self._slow_period}": round(self._last_slow, 4)
            if self._last_slow is not None
            else None,
            f"atr_{self._atr_period}": round(self._last_atr, 4)
            if self._last_atr is not None
            else None,
            "last_close": round(self._last_close, 2) if self._last_close is not None else None,
        }

    async def on_candle(
        self,
        symbol: str,
        instrument_type: InstrumentType,
        candle: CandleEvent,
    ) -> Signal | None:
        fast_ind, slow_ind, atr_ind = self._get_inds(symbol, candle.interval)
        fast_params = EMA.Parameters(period=self._fast_period)
        slow_params = EMA.Parameters(period=self._slow_period)
        atr_params = ATR.Parameters(period=self._atr_period)

        fast = await fast_ind.compute(fast_params)
        slow = await slow_ind.compute(slow_params)
        atr = await atr_ind.compute(atr_params)

        self._last_fast = fast
        self._last_slow = slow
        self._last_atr = atr
        self._last_close = candle.close

        self.chart("price", f"ema_{self._fast_period}", fast, candle.timestamp)
        self.chart("price", f"ema_{self._slow_period}", slow, candle.timestamp)
        self.chart("oscillators", f"atr_{self._atr_period}", atr, candle.timestamp)

        if fast is None or slow is None or atr is None or atr <= 0:
            self._prev_fast[symbol] = fast
            self._prev_slow[symbol] = slow
            return None

        prev_fast = self._prev_fast.get(symbol)
        prev_slow = self._prev_slow.get(symbol)
        self._prev_fast[symbol] = fast
        self._prev_slow[symbol] = slow

        if prev_fast is None or prev_slow is None:
            return None

        stop_distance = self._atr_multiplier * atr

        if prev_fast < prev_slow and fast > slow:
            logger.info(
                "EmaCrossover[%s]: BUY  fast=%.4f→%.4f slow=%.4f→%.4f stop=%.4f",
                symbol,
                prev_fast,
                fast,
                prev_slow,
                slow,
                stop_distance,
            )
            return Signal(
                symbol=symbol,
                instrument_type=instrument_type,
                side=Side.BUY,
                strategy_id=self.id,
                signal_type=SignalType.ENTRY,
                stop_distance=stop_distance,
                timestamp=candle.timestamp,
            )

        if prev_fast > prev_slow and fast < slow:
            logger.info(
                "EmaCrossover[%s]: SELL fast=%.4f→%.4f slow=%.4f→%.4f stop=%.4f",
                symbol,
                prev_fast,
                fast,
                prev_slow,
                slow,
                stop_distance,
            )
            return Signal(
                symbol=symbol,
                instrument_type=instrument_type,
                side=Side.SELL,
                strategy_id=self.id,
                signal_type=SignalType.ENTRY,
                stop_distance=stop_distance,
                timestamp=candle.timestamp,
            )

        return None
