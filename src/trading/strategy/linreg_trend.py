"""Linear Regression Slope Trend Strategy.

One of the top-performing indicators by mean ICIR (21.0) across 30-bar horizons.
Strategy: trend-following — enter when slope turns positive after negative (BUY),
exit/short when slope turns negative after positive (SELL).
"""

from __future__ import annotations

import logging

from trading.core.schemas import CandleEvent, InstrumentType, Side, SignalType
from quantindicators.library.atr import ATR
from quantindicators.library.linreg_slope import LinearRegressionSlope
from quantindicators.store import AbstractCandleStore
from trading.strategy.base import Signal, Strategy

logger = logging.getLogger(__name__)


class LinRegTrendStrategy(Strategy):
    """
    Trend-following via Linear Regression Slope.

    BUY  when slope crosses above *entry_threshold* (trend turning up).
    SELL when slope crosses below *-entry_threshold* (trend turning down).
    Stop distance = ATR × atr_multiplier.
    """

    alias = "linreg_trend"

    def __init__(
        self,
        period: int = 20,
        entry_threshold: float = 0.0,
        atr_period: int = 14,
        atr_multiplier: float = 1.5,
    ) -> None:
        self._period = period
        self._entry_threshold = entry_threshold
        self._atr_period = atr_period
        self._atr_multiplier = atr_multiplier
        self._store: AbstractCandleStore | None = None
        self._inds: dict[str, tuple[LinearRegressionSlope, ATR]] = {}
        self._prev_slope: dict[str, float | None] = {}
        self._last_slope: float | None = None
        self._last_atr: float | None = None

    def set_store(self, store: AbstractCandleStore) -> None:
        self._store = store

    def _get_inds(self, symbol: str, interval: str) -> tuple[LinearRegressionSlope, ATR]:
        if symbol not in self._inds:
            assert self._store is not None, "set_store() must be called before on_candle()"
            self._inds[symbol] = (
                LinearRegressionSlope(self._store, symbol, interval),
                ATR(self._store, symbol, interval),
            )
        return self._inds[symbol]

    def get_state(self) -> dict[str, object]:
        return {
            f"linreg_slope_{self._period}": round(self._last_slope, 4)
            if self._last_slope is not None
            else None,
            f"atr_{self._atr_period}": round(self._last_atr, 4)
            if self._last_atr is not None
            else None,
            "entry_threshold": self._entry_threshold,
        }

    async def on_candle(
        self,
        symbol: str,
        instrument_type: InstrumentType,
        candle: CandleEvent,
    ) -> Signal | None:
        slope_ind, atr_ind = self._get_inds(symbol, candle.interval)
        slope = await slope_ind.compute(LinearRegressionSlope.Parameters(period=self._period))
        atr = await atr_ind.compute(ATR.Parameters(period=self._atr_period))

        self._last_slope = slope
        self._last_atr = atr

        self.chart("oscillators", f"linreg_slope_{self._period}", slope, candle.timestamp)
        self.chart("oscillators", f"atr_{self._atr_period}", atr, candle.timestamp)

        prev_slope = self._prev_slope.get(symbol)
        self._prev_slope[symbol] = slope

        if slope is None or atr is None or atr <= 0 or prev_slope is None:
            return None

        stop_distance = self._atr_multiplier * atr

        if prev_slope <= self._entry_threshold and slope > self._entry_threshold:
            logger.info(
                "LinRegTrend[%s]: BUY  slope=%.4f→%.4f stop=%.4f",
                symbol, prev_slope, slope, stop_distance,
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

        if prev_slope >= -self._entry_threshold and slope < -self._entry_threshold:
            logger.info(
                "LinRegTrend[%s]: SELL slope=%.4f→%.4f stop=%.4f",
                symbol, prev_slope, slope, stop_distance,
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
