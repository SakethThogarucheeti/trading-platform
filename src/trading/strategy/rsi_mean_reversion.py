"""RSI Mean-Reversion Strategy."""

from __future__ import annotations

import logging

from trading.core.schemas import CandleEvent, InstrumentType, Side, SignalType
from quantindicators.library.atr import ATR
from quantindicators.library.rsi import RSI
from quantindicators.store import AbstractCandleStore
from trading.strategy.base import Signal, Strategy

logger = logging.getLogger(__name__)


class RsiMeanReversionStrategy(Strategy):
    """
    Buy the oversold bounce, sell the overbought fade.

    BUY  when RSI crosses back above *oversold* (was below, now above).
    SELL when RSI crosses back below *overbought* (was above, now below).
    Stop distance = ATR × atr_multiplier.
    """

    alias = "rsi_mean_reversion"

    def __init__(
        self,
        rsi_period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
        atr_period: int = 14,
        atr_multiplier: float = 1.5,
    ) -> None:
        if oversold >= overbought:
            raise ValueError(f"oversold ({oversold}) must be less than overbought ({overbought})")
        self._rsi_period = rsi_period
        self._oversold = oversold
        self._overbought = overbought
        self._atr_period = atr_period
        self._atr_multiplier = atr_multiplier
        self._store: AbstractCandleStore | None = None
        # indicator cache: symbol → (rsi, atr)
        self._inds: dict[str, tuple[RSI, ATR]] = {}
        self._prev_rsi: dict[str, float | None] = {}
        # last computed values for dashboard state
        self._last_rsi: float | None = None
        self._last_atr: float | None = None

    def set_store(self, store: AbstractCandleStore) -> None:
        self._store = store

    def _get_inds(self, symbol: str, interval: str) -> tuple[RSI, ATR]:
        if symbol not in self._inds:
            assert self._store is not None, "set_store() must be called before on_candle()"
            self._inds[symbol] = (
                RSI(self._store, symbol, interval),
                ATR(self._store, symbol, interval),
            )
        return self._inds[symbol]

    def get_state(self) -> dict[str, object]:
        return {
            f"rsi_{self._rsi_period}": round(self._last_rsi, 2)
            if self._last_rsi is not None
            else None,
            f"atr_{self._atr_period}": round(self._last_atr, 4)
            if self._last_atr is not None
            else None,
            "oversold": self._oversold,
            "overbought": self._overbought,
        }

    async def on_candle(
        self,
        symbol: str,
        instrument_type: InstrumentType,
        candle: CandleEvent,
    ) -> Signal | None:
        rsi_ind, atr_ind = self._get_inds(symbol, candle.interval)
        rsi_params = RSI.Parameters(period=self._rsi_period)
        atr_params = ATR.Parameters(period=self._atr_period)

        rsi = await rsi_ind.compute(rsi_params)
        atr = await atr_ind.compute(atr_params)

        self._last_rsi = rsi
        self._last_atr = atr

        self.chart("oscillators", f"rsi_{self._rsi_period}", rsi, candle.timestamp)
        self.chart("oscillators", f"atr_{self._atr_period}", atr, candle.timestamp)

        prev_rsi = self._prev_rsi.get(symbol)
        self._prev_rsi[symbol] = rsi

        if rsi is None or atr is None or atr <= 0 or prev_rsi is None:
            return None

        stop_distance = self._atr_multiplier * atr

        if prev_rsi <= self._oversold and rsi > self._oversold:
            logger.info(
                "RsiMeanReversion[%s]: BUY  rsi=%.1f→%.1f stop=%.4f",
                symbol,
                prev_rsi,
                rsi,
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

        if prev_rsi >= self._overbought and rsi < self._overbought:
            logger.info(
                "RsiMeanReversion[%s]: SELL rsi=%.1f→%.1f stop=%.4f",
                symbol,
                prev_rsi,
                rsi,
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
