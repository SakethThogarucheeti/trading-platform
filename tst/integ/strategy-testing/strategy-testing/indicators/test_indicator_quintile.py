"""
Indicator Quintile Analysis — monotonicity verification.

Q1 (lowest signal = most oversold) should return more than Q5 (most overbought).
Verifies whether signal strength monotonically predicts forward return direction.

Horizons tested: [5, 15, 30, 50] bars.

Run:
    cd tst/integ/strategy-testing

    uv run pytest strategy-testing/indicators/test_indicator_quintile.py -v -s --data-source=parquet
"""

from __future__ import annotations

import asyncio
import csv
import warnings
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

from trading.core.clock import SimulatedClock
from quantindicators.library.session_high_low_pct import SessionHighLowPct
from quantindicators.library.vwap import VWAP
from quantindicators.library.vwap_bands import VWAPBands
from quantindicators.polars_store import PolarsStore

_SYMBOLS = [
    "INFY",
    "TCS",
    "RELIANCE",
    "HDFCBANK",
    "ICICIBANK",
    "AXISBANK",
    "KOTAKBANK",
    "SBIN",
    "BAJFINANCE",
    "BAJAJFINSV",
    "WIPRO",
    "HCLTECH",
    "TECHM",
    "LT",
    "MARUTI",
    "SUNPHARMA",
    "DRREDDY",
    "DIVISLAB",
    "CIPLA",
    "TITAN",
    "ASIANPAINT",
    "NESTLEIND",
    "HINDUNILVR",
    "BRITANNIA",
    "POWERGRID",
    "NTPC",
    "ONGC",
    "COALINDIA",
    "ITC",
    "TATASTEEL",
]
_INTERVAL = "15min"
_WARMUP = 100
_HORIZONS = [5, 15, 30, 50]

_MONTH_END = datetime(2026, 4, 30, tzinfo=UTC)
_MONTH_START = _MONTH_END - timedelta(days=400)

_SESSION_CLASSES = (VWAP, VWAPBands, SessionHighLowPct)


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 4:
        return float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r, _ = stats.spearmanr(x, y)
    return float(r) if np.isfinite(r) else float("nan")


def _icir(ics: np.ndarray) -> float:
    valid = ics[~np.isnan(ics)]
    if len(valid) < 4:
        return float("nan")
    mean, std = valid.mean(), valid.std(ddof=1)
    return float(mean / std) if std > 0 else float("nan")


def _assign_quintiles(signals: np.ndarray) -> np.ndarray:
    """Assign each signal to quintile 0..4."""
    qs = np.percentile(signals, [20, 40, 60, 80])
    quintiles = np.digitize(signals, qs)  # returns 0..4
    return quintiles


def _quintile_stats(signals: np.ndarray, fwds: np.ndarray) -> tuple[list[float], list[float]]:
    """
    Returns (mean_returns_per_quintile, hitrate_per_quintile) as 5-element lists.
    Quintile 0 = lowest signal (most oversold), Quintile 4 = highest (most overbought).
    """
    quintiles = _assign_quintiles(signals)
    means: list[float] = []
    hitrates: list[float] = []
    for q in range(5):
        mask = quintiles == q
        if mask.sum() == 0:
            means.append(float("nan"))
            hitrates.append(float("nan"))
        else:
            f_q = fwds[mask]
            valid = f_q[np.isfinite(f_q)]
            means.append(float(valid.mean()) if len(valid) > 0 else float("nan"))
            hitrates.append(float((valid > 0).mean()) if len(valid) > 0 else float("nan"))
    return means, hitrates


# ---------------------------------------------------------------------------
# Worker — module-level for pickling
# ---------------------------------------------------------------------------


def _worker_quintile(args: tuple[str, list[dict]]) -> dict[str, Any]:
    """
    Collect (signal, fwd_h) pairs for each indicator at each horizon.
    Returns {label: {horizon: [(sig, fwd), ...]}}
    """
    symbol, rows = args
    print(f"  {symbol} ...", flush=True)

    async def _run() -> dict[str, Any]:
        from testing.indicators.catalogue import load_catalogue

        catalogue = load_catalogue("15min")
        closes = np.array([r["close"] for r in rows], dtype=float)

        # Pre-compute forward returns for all horizons
        fwd_returns: dict[int, np.ndarray] = {}
        for h in _HORIZONS:
            ret = np.full(len(closes), np.nan)
            for i in range(len(closes) - h):
                ret[i] = (closes[i + h] - closes[i]) / closes[i]
            fwd_returns[h] = ret

        result: dict[str, Any] = {}

        for label, cls, params, extractor in catalogue:
            if cls is None:
                continue  # skip EMA_cross_9_21

            clock = SimulatedClock()
            store = PolarsStore(maxlen=500)

            if cls in _SESSION_CLASSES:
                ind = cls(store, symbol, _INTERVAL, clock)
            else:
                ind = cls(store, symbol, _INTERVAL)

            pairs_by_h: dict[int, list[tuple[float, float]]] = {h: [] for h in _HORIZONS}

            for i, row in enumerate(rows):
                clock.advance(row["ts"])
                store.push(symbol, _INTERVAL, row)

                if i < _WARMUP:
                    continue

                try:
                    if extractor == "macd_hist":
                        r = await ind.compute_full(params)
                        sig = r[2] if r is not None else None
                    elif extractor == "psar":
                        r = await ind.compute_full(params)
                        sig = (1.0 if r[1] else -1.0) if r is not None else None
                    elif extractor == "vwap_dev":
                        vwap = await ind.compute(params)
                        close = float(row["close"])
                        sig = (vwap - close) / close if (vwap is not None and close != 0) else None
                    elif extractor == "bollinger_pctb":
                        r = await ind.compute_full(params)
                        sig = -r[4] if r is not None else None
                    elif extractor == "keltner_pct":
                        r = await ind.compute_full(params)
                        if r is None:
                            sig = None
                        else:
                            upper, middle, lower = r
                            width = upper - lower
                            sig = -(middle - lower) / width if width != 0 else None
                    elif extractor == "donchian_pct":
                        r = await ind.compute_full(params)
                        if r is None:
                            sig = None
                        else:
                            upper, middle, lower = r
                            width = upper - lower
                            sig = -(middle - lower) / width if width != 0 else None
                    else:
                        raw = await ind.compute(params)
                        if raw is None:
                            sig = None
                        elif extractor == "neg":
                            sig = -raw
                        elif extractor == "rsi":
                            sig = 100.0 - raw
                        else:
                            sig = raw
                except Exception:
                    sig = None

                if sig is None or not np.isfinite(sig):
                    continue

                for h in _HORIZONS:
                    fwd = float(fwd_returns[h][i])
                    if np.isfinite(fwd):
                        pairs_by_h[h].append((float(sig), fwd))

            result[label] = {h: pairs for h, pairs in pairs_by_h.items()}

        return result

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------


async def test_indicator_quintile_analysis(make_store) -> None:
    """
    Verify monotonicity: Q1 (lowest signal) should have higher returns than Q5.
    Pools (signal, fwd_return) across all symbols, bins into quintiles.
    Writes indicator_quintile_results.csv and indicator_quintile.html.
    """
    symbol_rows: list[tuple[str, list[dict]]] = []
    for symbol in _SYMBOLS:
        _store, rows = make_store(symbol, _INTERVAL, _MONTH_START, _MONTH_END)
        if not rows:
            print(f"  WARNING: no data for {symbol}")
            continue
        symbol_rows.append((symbol, rows))

    print(f"\n  Dispatching {len(symbol_rows)} symbols to process pool ...")

    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor() as executor:
        futures = [
            loop.run_in_executor(executor, _worker_quintile, (sym, rows))
            for sym, rows in symbol_rows
        ]
        per_symbol: list[dict[str, Any]] = await asyncio.gather(*futures)

    # Pool pairs across symbols per label per horizon
    pooled: dict[str, dict[int, list[tuple[float, float]]]] = defaultdict(
        lambda: {h: [] for h in _HORIZONS}
    )
    for sym_result in per_symbol:
        for label, h_pairs in sym_result.items():
            for h, pairs in h_pairs.items():
                pooled[label][h].extend(pairs)

    # Compute quintile stats
    summary_rows: list[dict[str, Any]] = []
    _nan = float("nan")

    for label in sorted(pooled.keys()):
        for h in _HORIZONS:
            pairs = pooled[label][h]
            if len(pairs) < 50:
                continue
            sigs = np.array([p[0] for p in pairs])
            fwds = np.array([p[1] for p in pairs])

            means, hitrates = _quintile_stats(sigs, fwds)
            q1_ret = means[0]
            q5_ret = means[4]
            spread = (q1_ret - q5_ret) if (np.isfinite(q1_ret) and np.isfinite(q5_ret)) else _nan

            row: dict[str, Any] = {
                "indicator": label,
                "horizon": h,
                "n": len(pairs),
                "Q1_ret": means[0],
                "Q2_ret": means[1],
                "Q3_ret": means[2],
                "Q4_ret": means[3],
                "Q5_ret": means[4],
                "spread": spread,
                "Q1_hitrate": hitrates[0],
                "Q5_hitrate": hitrates[4],
            }
            summary_rows.append(row)

    # Sort by |spread| at 5-bar horizon
    label_spread5: dict[str, float] = {}
    for r in summary_rows:
        if r["horizon"] == 5 and np.isfinite(r["spread"]):
            label_spread5[r["indicator"]] = abs(r["spread"])

    summary_rows.sort(
        key=lambda r: (
            -label_spread5.get(r["indicator"], 0.0),
            r["indicator"],
            r["horizon"],
        )
    )

    # Print summary for 5-bar horizon only
    print(f"\n{'=' * 100}")
    print(
        f"  Indicator Quintile Analysis  |  {_MONTH_START.date()} to {_MONTH_END.date()}  |  {_INTERVAL}  |  {len(_SYMBOLS)} symbols"
    )
    print("  (Showing horizon=5 rows sorted by |spread|)")
    print(f"{'=' * 100}")
    print(
        f"  {'Indicator':<22}  {'Q1':>8}  {'Q2':>8}  {'Q3':>8}  {'Q4':>8}  {'Q5':>8}  {'Spread':>9}  {'Q1_hr':>7}  {'Q5_hr':>7}"
    )
    print(f"  {'-' * 95}")
    for r in summary_rows:
        if r["horizon"] != 5:
            continue

        def _fmt(v: float) -> str:
            return f"{v:+.4f}" if np.isfinite(v) else "     nan"

        def _fmtp(v: float) -> str:
            return f"{v:.3f}" if np.isfinite(v) else "    nan"

        print(
            f"  {r['indicator']:<22}  {_fmt(r['Q1_ret'])}  {_fmt(r['Q2_ret'])}  "
            f"{_fmt(r['Q3_ret'])}  {_fmt(r['Q4_ret'])}  {_fmt(r['Q5_ret'])}  "
            f"{_fmt(r['spread'])}  {_fmtp(r['Q1_hitrate'])}  {_fmtp(r['Q5_hitrate'])}"
        )
    print(f"{'=' * 100}")

    # Write CSV
    reports_dir = Path(__file__).parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    csv_path = reports_dir / "indicator_quintile_results.csv"

    if summary_rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            writer.writeheader()
            writer.writerows(summary_rows)
    print(f"\n  CSV written to {csv_path}")

    # Write HTML
    def _spread_color(v: float) -> str:
        if not np.isfinite(v):
            return "#ffffff"
        return "#c8e6c9" if v > 0 else "#ffcdd2"

    def _ret_color(v: float) -> str:
        if not np.isfinite(v):
            return "#ffffff"
        if v > 0.001:
            return "#c8e6c9"
        if v < -0.001:
            return "#ffcdd2"
        return "#ffffff"

    col_keys = [
        "indicator",
        "horizon",
        "n",
        "Q1_ret",
        "Q2_ret",
        "Q3_ret",
        "Q4_ret",
        "Q5_ret",
        "spread",
        "Q1_hitrate",
        "Q5_hitrate",
    ]
    th = "".join(f"<th>{c}</th>" for c in col_keys)
    html_rows = []
    for r in summary_rows:
        cells = []
        for key in col_keys:
            v = r.get(key, _nan)
            if key == "indicator":
                cells.append(f"<td><b>{v}</b></td>")
            elif key in ("horizon", "n"):
                cells.append(f"<td>{int(v) if np.isfinite(float(v)) else v}</td>")
            elif key == "spread":
                color = _spread_color(float(v))
                fmt = (
                    f"{float(v):+.4f}"
                    if isinstance(v, (int, float)) and np.isfinite(float(v))
                    else "nan"
                )
                cells.append(f'<td style="background:{color};font-weight:bold">{fmt}</td>')
            elif key in ("Q1_ret", "Q2_ret", "Q3_ret", "Q4_ret", "Q5_ret"):
                fv = float(v)
                color = _ret_color(fv)
                fmt = f"{fv:+.4f}" if np.isfinite(fv) else "nan"
                cells.append(f'<td style="background:{color}">{fmt}</td>')
            else:
                fv = float(v)
                fmt = f"{fv:.3f}" if np.isfinite(fv) else "nan"
                cells.append(f"<td>{fmt}</td>")
        html_rows.append(f"<tr>{''.join(cells)}</tr>")

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Indicator Quintile Analysis — 15min</title>
<style>
  body {{ font-family: monospace; font-size: 13px; margin: 20px; }}
  h2 {{ margin-bottom: 8px; }}
  table {{ border-collapse: collapse; }}
  th, td {{ border: 1px solid #ccc; padding: 4px 8px; white-space: nowrap; }}
  th {{ background: #e0e0e0; position: sticky; top: 0; }}
  tr:hover {{ background: #f5f5f5 !important; }}
</style>
</head>
<body>
<h2>Indicator Quintile Analysis — {_INTERVAL} | {_MONTH_START.date()} to {_MONTH_END.date()} | {len(_SYMBOLS)} symbols</h2>
<p>Q1 = lowest signal (oversold), Q5 = highest (overbought). Green spread = Q1 &gt; Q5 (mean-reversion works).</p>
<p>Sorted by |spread| at 5-bar horizon. Horizons shown: {_HORIZONS}.</p>
<table>
<thead><tr>{th}</tr></thead>
<tbody>
{"".join(html_rows)}
</tbody>
</table>
</body>
</html>
"""
    html_path = reports_dir / "indicator_quintile.html"
    html_path.write_text(html_content, encoding="utf-8")
    print(f"  HTML report written to {html_path}")

    # Soft assertion
    assert len(summary_rows) > 0, "Expected quintile analysis to produce at least one row"
    finite_spreads = [r["spread"] for r in summary_rows if np.isfinite(r["spread"])]
    assert len(finite_spreads) > 0, "Expected at least one indicator with finite Q-spread"
