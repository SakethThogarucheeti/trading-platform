"""DPO Mean-Reversion Strategy.

Second-best indicator by mean ICIR (20.18) — Detrended Price Oscillator.
DPO removes the dominant trend to expose price cycles. Strategy: mean-revert
when DPO is at an extreme, confirmed by a momentum turn on the next bar.
"""

from __future__ import annotations

import logging

from trading.core.schemas import CandleEvent, InstrumentType, Side, SignalType
from quantindicators.library.atr import ATR
from quantindicators.library.dpo import DPO
from quantindicators.store import AbstractCandleStore
from trading.strategy.base import Signal, Strategy

logger = logging.getLogger(__name__)


class DpoMeanReversionStrategy(Strategy):
    """
    Mean-reversion using the Detrended Price Oscillator.

    DPO = close - SMA(close) shifted (period // 2 + 1) bars back.
    Positive DPO = overbought (above detrended mean), Negative = oversold.

    BUY  when DPO was below *-dpo_threshold* and the current DPO > previous DPO
         (cycle starting to turn up from oversold).
    SELL when DPO was above *dpo_threshold* and the current DPO < previous DPO
         (cycle starting to turn down from overbought).

    Stop distance = ATR × atr_multiplier.
    """

    alias = "dpo_mean_reversion"

    def __init__(
        self,
        period: int = 20,
        dpo_threshold: float = 0.0,
        atr_period: int = 14,
        atr_multiplier: float = 1.5,
    ) -> None:
        self._period = period
        self._dpo_threshold = dpo_threshold
        self._atr_period = atr_period
        self._atr_multiplier = atr_multiplier
        self._store: AbstractCandleStore | None = None
        self._inds: dict[str, tuple[DPO, ATR]] = {}
        self._prev_dpo: dict[str, float | None] = {}
        self._last_dpo: float | None = None
        self._last_atr: float | None = None

    def set_store(self, store: AbstractCandleStore) -> None:
        self._store = store

    def _get_inds(self, symbol: str, interval: str) -> tuple[DPO, ATR]:
        if symbol not in self._inds:
            assert self._store is not None, "set_store() must be called before on_candle()"
            self._inds[symbol] = (
                DPO(self._store, symbol, interval),
                ATR(self._store, symbol, interval),
            )
        return self._inds[symbol]

    def get_state(self) -> dict[str, object]:
        return {
            f"dpo_{self._period}": round(self._last_dpo, 4)
            if self._last_dpo is not None
            else None,
            f"atr_{self._atr_period}": round(self._last_atr, 4)
            if self._last_atr is not None
            else None,
            "dpo_threshold": self._dpo_threshold,
        }

    async def on_candle(
        self,
        symbol: str,
        instrument_type: InstrumentType,
        candle: CandleEvent,
    ) -> Signal | None:
        dpo_ind, atr_ind = self._get_inds(symbol, candle.interval)
        dpo = await dpo_ind.compute(DPO.Parameters(period=self._period))
        atr = await atr_ind.compute(ATR.Parameters(period=self._atr_period))

        self._last_dpo = dpo
        self._last_atr = atr

        self.chart("oscillators", f"dpo_{self._period}", dpo, candle.timestamp)
        self.chart("oscillators", f"atr_{self._atr_period}", atr, candle.timestamp)

        prev_dpo = self._prev_dpo.get(symbol)
        self._prev_dpo[symbol] = dpo

        if dpo is None or atr is None or atr <= 0 or prev_dpo is None:
            return None

        stop_distance = self._atr_multiplier * atr

        # Oversold: DPO was below -threshold and is now turning up
        if prev_dpo < -self._dpo_threshold and dpo > prev_dpo:
            logger.info(
                "DpoMeanReversion[%s]: BUY  dpo=%.4f→%.4f stop=%.4f",
                symbol, prev_dpo, dpo, stop_distance,
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

        # Overbought: DPO was above +threshold and is now turning down
        if prev_dpo > self._dpo_threshold and dpo < prev_dpo:
            logger.info(
                "DpoMeanReversion[%s]: SELL dpo=%.4f→%.4f stop=%.4f",
                symbol, prev_dpo, dpo, stop_distance,
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
