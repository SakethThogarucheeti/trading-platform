"""
Smoke test: all indicators compute without error after sufficient bars.

Uses the make_store fixture (synthetic data by default — no files needed).
Each parametrized test loads one month of 15min bars into a real PolarsStore,
constructs the indicator with (store, symbol, interval), and asserts that
compute(params) either returns a finite float or None (never raises, never
returns NaN/inf).

Run:
    cd tst/integ/strategy-testing
    python -m pytest strategy-testing/indicators/test_indicator_smoke.py -v
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from quantindicators.library.adx import ADX
from quantindicators.library.aroon import Aroon
from quantindicators.library.atr import ATR
from quantindicators.library.bollinger import BollingerBands
from quantindicators.library.candle_body_ratio import CandleBodyRatio
from quantindicators.library.cci import CCI
from quantindicators.library.chaikin_volatility import ChaikinVolatility
from quantindicators.library.chandelier_exit import ChandelierExit
from quantindicators.library.cmf import CMF
from quantindicators.library.connors_rsi import ConnorsRSI
from quantindicators.library.coppock_curve import CoppockCurve
from quantindicators.library.distance_from_ma import DistanceFromMA
from quantindicators.library.donchian import DonchianChannels
from quantindicators.library.dpo import DPO
from quantindicators.library.elder_ray import ElderRay
from quantindicators.library.ema import EMA
from quantindicators.library.fisher_transform import FisherTransform
from quantindicators.library.gap import GapSize
from quantindicators.library.historical_volatility import HistoricalVolatility
from quantindicators.library.inside_bar import InsideBar
from quantindicators.library.keltner import KeltnerChannels
from quantindicators.library.linreg_slope import LinearRegressionSlope
from quantindicators.library.macd import MACD
from quantindicators.library.mean_reversion_score import MeanReversionScore
from quantindicators.library.mfi import MFI
from quantindicators.library.momentum import Momentum
from quantindicators.library.normalized_atr import NormalizedATR
from quantindicators.library.obv import OBV
from quantindicators.library.opening_range import OpeningRangePosition
from quantindicators.library.parabolic_sar import ParabolicSAR
from quantindicators.library.price_percentile import PricePercentile
from quantindicators.library.price_vs_52w_high import PriceVs52wHigh
from quantindicators.library.pvt import PVT
from quantindicators.library.roc import ROC
from quantindicators.library.rsi import RSI
from quantindicators.library.rsi_divergence import RSIDivergence
from quantindicators.library.rvol import RVOL
from quantindicators.library.session_high_low_pct import SessionHighLowPct
from quantindicators.library.sma import SMA
from quantindicators.library.squeeze_momentum import SqueezeMomentum
from quantindicators.library.stochastic import Stochastic
from quantindicators.library.stochastic_rsi import StochasticRSI
from quantindicators.library.supertrend import Supertrend
from quantindicators.library.tsi import TSI
from quantindicators.library.ultimate_oscillator import UltimateOscillator
from quantindicators.library.upper_shadow_ratio import UpperShadowRatio
from quantindicators.library.volatility_ratio import VolatilityRatio
from quantindicators.library.vroc import VROC
from quantindicators.library.vwap import VWAP
from quantindicators.library.vwap_bands import VWAPBands
from quantindicators.library.vwma import VWMA
from quantindicators.library.weekly_rsi import WeeklyRSI
from quantindicators.library.williams_r import WilliamsR

_SYMBOL = "INFY"
_INTERVAL = "15min"


def _catalogue() -> list[tuple[str, type, Any]]:
    """Return (label, IndicatorClass, params) triples for every indicator."""
    return [
        ("EMA_9", EMA, EMA.Parameters(period=9)),
        ("SMA_20", SMA, SMA.Parameters(period=20)),
        ("RSI_14", RSI, RSI.Parameters(period=14)),
        ("ATR_14", ATR, ATR.Parameters(period=14)),
        ("ADX_14", ADX, ADX.Parameters(period=14)),
        ("MACD_12_26_9", MACD, MACD.Parameters(fast=12, slow=26, signal=9)),
        ("BollingerBands", BollingerBands, BollingerBands.Parameters(period=20, k=2.0)),
        (
            "KeltnerChannels",
            KeltnerChannels,
            KeltnerChannels.Parameters(ema_period=20, atr_period=10, k=2.0),
        ),
        ("DonchianChannels", DonchianChannels, DonchianChannels.Parameters(period=20)),
        ("Stochastic_14_3", Stochastic, Stochastic.Parameters(k_period=14, d_period=3)),
        ("WilliamsR_14", WilliamsR, WilliamsR.Parameters(period=14)),
        ("CCI_20", CCI, CCI.Parameters(period=20)),
        ("MFI_14", MFI, MFI.Parameters(period=14)),
        ("CMF_20", CMF, CMF.Parameters(period=20)),
        ("OBV_20", OBV, OBV.Parameters(period=20)),
        ("VWMA_20", VWMA, VWMA.Parameters(period=20)),
        ("Momentum_10", Momentum, Momentum.Parameters(period=10)),
        ("ROC_10", ROC, ROC.Parameters(period=10)),
        (
            "ChaikinVol_10",
            ChaikinVolatility,
            ChaikinVolatility.Parameters(ema_period=10, roc_period=10),
        ),
        ("HistVol_20", HistoricalVolatility, HistoricalVolatility.Parameters(period=20)),
        ("Supertrend_10", Supertrend, Supertrend.Parameters(period=10, multiplier=3.0)),
        ("ParabolicSAR", ParabolicSAR, ParabolicSAR.Parameters()),
        ("ConnorsRSI", ConnorsRSI, ConnorsRSI.Parameters()),
        ("FisherTransform", FisherTransform, FisherTransform.Parameters(period=10)),
        ("UltimateOsc", UltimateOscillator, UltimateOscillator.Parameters()),
        ("DPO_20", DPO, DPO.Parameters(period=20)),
        ("TSI", TSI, TSI.Parameters()),
        ("GapSize", GapSize, GapSize.Parameters()),
        ("OpeningRange", OpeningRangePosition, OpeningRangePosition.Parameters(range_bars=4)),
        ("RVOL_20", RVOL, RVOL.Parameters(period=20)),
        ("VROC_14", VROC, VROC.Parameters(period=14)),
        ("PVT_20", PVT, PVT.Parameters(period=20)),
        ("VolRatio", VolatilityRatio, VolatilityRatio.Parameters()),
        ("SqueezeMom", SqueezeMomentum, SqueezeMomentum.Parameters()),
        ("NormATR_14", NormalizedATR, NormalizedATR.Parameters(period=14)),
        # Swing trading indicators
        ("StochRSI_14", StochasticRSI, StochasticRSI.Parameters(rsi_period=14, stoch_period=14)),
        (
            "RSIDivergence",
            RSIDivergence,
            RSIDivergence.Parameters(rsi_period=14, divergence_window=10),
        ),
        ("CoppockCurve", CoppockCurve, CoppockCurve.Parameters()),
        ("ElderRay_13", ElderRay, ElderRay.Parameters(period=13)),
        ("Aroon_25", Aroon, Aroon.Parameters(period=25)),
        ("PricePercentile", PricePercentile, PricePercentile.Parameters(period=50)),
        ("DistFromMA_20", DistanceFromMA, DistanceFromMA.Parameters(period=20)),
        ("LinRegSlope_20", LinearRegressionSlope, LinearRegressionSlope.Parameters(period=20)),
        ("MeanRevScore", MeanReversionScore, MeanReversionScore.Parameters()),
        ("Chandelier_22", ChandelierExit, ChandelierExit.Parameters(period=22)),
        ("CandleBody_5", CandleBodyRatio, CandleBodyRatio.Parameters(period=5)),
        ("UpperShadow_5", UpperShadowRatio, UpperShadowRatio.Parameters(period=5)),
        ("InsideBar_10", InsideBar, InsideBar.Parameters(period=10)),
        ("WeeklyRSI_14", WeeklyRSI, WeeklyRSI.Parameters(rsi_period=14)),
        ("PriceVs52w", PriceVs52wHigh, PriceVs52wHigh.Parameters(period=252)),
    ]


def _session_catalogue() -> list[tuple[str, type, Any]]:
    """Session-aware indicators that need a clock in __init__."""
    return [
        ("VWAP", VWAP, VWAP.Parameters()),
        ("VWAPBands", VWAPBands, VWAPBands.Parameters()),
        ("SessionHLPct", SessionHighLowPct, SessionHighLowPct.Parameters()),
    ]


@pytest.mark.parametrize(
    "label",
    [label for label, _, _ in _catalogue()],
)
async def test_indicator_no_error_after_warmup(label: str, make_store) -> None:
    """
    Indicator must not raise and must return float | None (never NaN/inf).

    Some indicators legitimately return None (e.g. RSI on a perfectly flat
    series).  That is acceptable — the assertion is about absence of errors
    and absence of non-finite garbage values.
    """
    store, rows = make_store(_SYMBOL, _INTERVAL)
    assert rows, f"make_store returned no rows for {_SYMBOL}/{_INTERVAL}"

    cat = {lbl: (cls, params) for lbl, cls, params in _catalogue()}
    cls, params = cat[label]
    ind = cls(store, _SYMBOL, _INTERVAL)

    result = await ind.compute(params)

    assert result is None or (isinstance(result, float) and math.isfinite(result)), (
        f"{label}.compute() returned invalid value: {result!r}"
    )


@pytest.mark.parametrize(
    "label",
    [label for label, _, _ in _session_catalogue()],
)
async def test_session_indicator_no_error(label: str, make_store, simulated_clock) -> None:
    """Session-aware indicators tested separately (require clock in __init__)."""
    store, rows = make_store(_SYMBOL, _INTERVAL)
    assert rows, f"make_store returned no rows for {_SYMBOL}/{_INTERVAL}"

    cat = {lbl: (cls, params) for lbl, cls, params in _session_catalogue()}
    cls, params = cat[label]
    ind = cls(store, _SYMBOL, _INTERVAL, simulated_clock)

    result = await ind.compute(params)

    assert result is None or (isinstance(result, float) and math.isfinite(result)), (
        f"{label}.compute() returned invalid value: {result!r}"
    )


async def test_all_indicators_produce_values(make_store, simulated_clock) -> None:
    """At least half of all indicators must return a non-None value after warmup."""
    store, rows = make_store(_SYMBOL, _INTERVAL)
    assert rows, "make_store returned no rows"

    all_entries = _catalogue() + [(lbl, cls, params) for lbl, cls, params in _session_catalogue()]
    none_count = 0
    errors: list[str] = []

    for label, cls, params in all_entries:
        # Session-aware indicators need clock
        try:
            if cls in (VWAP, VWAPBands, SessionHighLowPct):
                ind = cls(store, _SYMBOL, _INTERVAL, simulated_clock)
            else:
                ind = cls(store, _SYMBOL, _INTERVAL)
            result = await ind.compute(params)
        except Exception as exc:
            errors.append(f"{label}: raised {exc!r}")
            continue
        if result is None:
            none_count += 1
        elif not math.isfinite(result):
            errors.append(f"{label}: returned non-finite {result!r}")

    assert not errors, "Indicators raised errors or returned non-finite:\n" + "\n".join(errors)
    total = len(all_entries)
    assert none_count < total // 2, (
        f"Too many indicators returned None ({none_count}/{total}). "
        "Warmup may be insufficient or data too short."
    )
