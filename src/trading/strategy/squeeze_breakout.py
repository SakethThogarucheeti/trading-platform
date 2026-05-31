"""Squeeze Momentum Breakout Strategy.

Third-best indicator by mean ICIR (8.78) — Bollinger/Keltner squeeze + momentum.
Squeeze = volatility compression (BBands inside Keltner Channels). When compression
releases, momentum determines direction. Strategy: enter on momentum zero-cross
after a squeeze has been detected.
"""

from __future__ import annotations

import logging
from typing import TypedDict, cast

from quantindicators.library.atr import ATR
from quantindicators.library.squeeze_momentum import SqueezeMomentum
from quantindicators.store import AbstractCandleStore

from trading.core.schemas import CandleEvent, InstrumentType, Side, SignalType
from trading.strategy.base import Signal, Strategy


class _State(TypedDict, total=False):
    prev_momentum: dict[str, float | None]
    bars_since_squeeze: dict[str, int]
    last_momentum: float | None
    last_atr: float | None

logger = logging.getLogger(__name__)


class SqueezeBreakoutStrategy(Strategy):
    """
    Squeeze Momentum Breakout.

    Enters when the Squeeze Momentum value crosses zero after volatility
    compression (Bollinger Bands inside Keltner Channels). The squeeze
    state is tracked for *squeeze_lookback* bars to avoid late entries.

    BUY  when momentum crosses above 0 within squeeze_lookback bars of a squeeze.
    SELL when momentum crosses below 0 within squeeze_lookback bars of a squeeze.

    Stop distance = ATR × atr_multiplier.
    """

    alias = "squeeze_breakout"

    def __init__(
        self,
        period: int = 20,
        bb_k: float = 2.0,
        kc_k: float = 1.5,
        squeeze_lookback: int = 5,
        atr_period: int = 14,
        atr_multiplier: float = 1.5,
    ) -> None:
        self._period = period
        self._bb_k = bb_k
        self._kc_k = kc_k
        self._squeeze_lookback = squeeze_lookback
        self._atr_period = atr_period
        self._atr_multiplier = atr_multiplier
        self._store: AbstractCandleStore | None = None
        self._inds: dict[str, tuple[SqueezeMomentum, ATR]] = {}
        self._prev_momentum: dict[str, float | None] = {}
        # Bars since last squeeze fired (per symbol); None = never squeezed
        self._bars_since_squeeze: dict[str, int] = {}
        self._last_momentum: float | None = None
        self._last_atr: float | None = None

    def set_store(self, store: AbstractCandleStore) -> None:
        self._store = store

    def _get_inds(self, symbol: str, interval: str) -> tuple[SqueezeMomentum, ATR]:
        if symbol not in self._inds:
            assert self._store is not None, "set_store() must be called before on_candle()"
            self._inds[symbol] = (
                SqueezeMomentum(self._store, symbol, interval),
                ATR(self._store, symbol, interval),
            )
        return self._inds[symbol]

    def get_state(self) -> dict[str, object]:
        return {
            "squeeze_momentum": round(self._last_momentum, 4)
            if self._last_momentum is not None
            else None,
            f"atr_{self._atr_period}": round(self._last_atr, 4)
            if self._last_atr is not None
            else None,
            "squeeze_lookback": self._squeeze_lookback,
        }

    def rolling_state(self) -> dict[str, object]:
        return {
            "prev_momentum": self._prev_momentum,
            "bars_since_squeeze": self._bars_since_squeeze,
            "last_momentum": self._last_momentum,
            "last_atr": self._last_atr,
        }

    async def restore_from_state(self, state: dict[str, object]) -> bool:
        try:
            s = cast(_State, state)
            self._prev_momentum = dict(s["prev_momentum"])
            self._bars_since_squeeze = {k: int(v) for k, v in s["bars_since_squeeze"].items()}
            self._last_momentum = s.get("last_momentum")
            self._last_atr = s.get("last_atr")
            return True
        except (KeyError, TypeError, AttributeError):
            return False

    async def on_candle(
        self,
        symbol: str,
        instrument_type: InstrumentType,
        candle: CandleEvent,
    ) -> Signal | None:
        squeeze_ind, atr_ind = self._get_inds(symbol, candle.interval)
        params = SqueezeMomentum.Parameters(
            period=self._period, bb_k=self._bb_k, kc_k=self._kc_k
        )
        momentum = await squeeze_ind.compute(params)
        atr = await atr_ind.compute(ATR.Parameters(period=self._atr_period))

        self._last_momentum = momentum
        self._last_atr = atr

        self.chart("oscillators", "squeeze_momentum", momentum, candle.timestamp)
        self.chart("oscillators", f"atr_{self._atr_period}", atr, candle.timestamp)

        # Track squeeze state (accessed via private attr set inside SqueezeMomentum.compute)
        squeeze_on: bool = getattr(squeeze_ind, "_squeeze_on", False)
        bars_since = self._bars_since_squeeze.get(symbol, self._squeeze_lookback + 1)

        if squeeze_on:
            self._bars_since_squeeze[symbol] = 0
        else:
            self._bars_since_squeeze[symbol] = bars_since + 1

        prev_momentum = self._prev_momentum.get(symbol)
        self._prev_momentum[symbol] = momentum

        if momentum is None or atr is None or atr <= 0 or prev_momentum is None:
            return None

        # Only trade within squeeze_lookback bars after a squeeze released
        within_lookback = self._bars_since_squeeze[symbol] <= self._squeeze_lookback
        if not within_lookback:
            return None

        stop_distance = self._atr_multiplier * atr

        if prev_momentum <= 0 < momentum:
            logger.info(
                "SqueezeBreakout[%s]: BUY  momentum=%.4f→%.4f stop=%.4f",
                symbol, prev_momentum, momentum, stop_distance,
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

        if prev_momentum >= 0 > momentum:
            logger.info(
                "SqueezeBreakout[%s]: SELL momentum=%.4f→%.4f stop=%.4f",
                symbol, prev_momentum, momentum, stop_distance,
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
