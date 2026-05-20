"""
Indicator Pairwise Spearman Correlation Matrix.

For each indicator, collects signal vectors across all symbols and bars, then
computes N×N pairwise Spearman rank correlations to identify redundant signals.

High |corr| (>0.8) → indicators carry similar information (redundant).
Low |corr| (<0.6)  → indicators provide independent signal.

Run:
    cd tst/integ/strategy-testing

    uv run pytest strategy-testing/indicators/test_indicator_correlation.py -v -s --data-source=parquet
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
from quantindicators.library.ema import EMA
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

_MONTH_END = datetime(2026, 4, 30, tzinfo=UTC)
_MONTH_START = _MONTH_END - timedelta(days=400)

_SESSION_CLASSES = (VWAP, VWAPBands, SessionHighLowPct)

_MIN_SIGNALS = 100  # minimum valid signals per indicator across all symbols


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


# ---------------------------------------------------------------------------
# Per-indicator signal collection (same core as _evaluate_indicator in IC test)
# ---------------------------------------------------------------------------


async def _collect_signals(
    label: str,
    cls: Any,
    params: Any,
    extractor: Any,
    rows: list[dict],
    symbol: str,
    clock: SimulatedClock,
) -> list[float]:
    """Collect all valid signal values for this indicator over the bar series."""
    store = PolarsStore(maxlen=500)

    is_ema_cross = label == "EMA_cross_9_21"
    if is_ema_cross:
        fast_ind = EMA(store, symbol, _INTERVAL)
        slow_ind = EMA(store, symbol, _INTERVAL)
        fast_params = EMA.Parameters(period=9)
        slow_params = EMA.Parameters(period=21)
        prev_fast: float | None = None
        prev_slow: float | None = None
    elif cls in _SESSION_CLASSES:
        ind = cls(store, symbol, _INTERVAL, clock)
    else:
        ind = cls(store, symbol, _INTERVAL)

    signals: list[float] = []

    for i, row in enumerate(rows):
        clock.advance(row["ts"])
        store.push(symbol, _INTERVAL, row)

        if i < _WARMUP:
            if is_ema_cross:
                prev_fast = await fast_ind.compute(fast_params)
                prev_slow = await slow_ind.compute(slow_params)
            continue

        try:
            if is_ema_cross:
                fast = await fast_ind.compute(fast_params)
                slow = await slow_ind.compute(slow_params)
                sig = (
                    (fast - slow)
                    if (
                        fast is not None
                        and slow is not None
                        and prev_fast is not None
                        and prev_slow is not None
                    )
                    else None
                )
                prev_fast, prev_slow = fast, slow

            elif extractor == "macd_hist":
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
                if r is None:
                    sig = None
                else:
                    _upper, _mid, _lower, _bw, pct_b = r
                    sig = -pct_b

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
                    sig = raw  # "id"

        except Exception:
            sig = None

        if sig is not None and np.isfinite(sig):
            signals.append(float(sig))

    return signals


# ---------------------------------------------------------------------------
# Worker — module-level for pickling
# ---------------------------------------------------------------------------


def _worker_corr(args: tuple[str, list[dict]]) -> dict[str, list[float]]:
    """
    Collect signal vectors for all catalogue indicators for one symbol.
    Returns {label: [signal, ...]} with only finite floats.
    """
    symbol, rows = args
    print(f"  {symbol} ...", flush=True)

    async def _run() -> dict[str, list[float]]:
        from testing.indicators.catalogue import load_catalogue

        catalogue = load_catalogue("15min")
        result: dict[str, list[float]] = {}
        for label, cls, params, extractor in catalogue:
            if cls is None:
                continue  # skip EMA_cross_9_21 (alias=null)
            clock = SimulatedClock()
            sigs = await _collect_signals(label, cls, params, extractor, rows, symbol, clock)
            result[label] = sigs
        return result

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------


async def test_indicator_correlation_matrix(make_store) -> None:
    """
    Compute N×N pairwise Spearman rank correlation between all indicator signals.
    Identifies redundant pairs (|corr| > 0.8) and independent pairs (|corr| < 0.6).
    Writes indicator_correlation_results.csv and indicator_correlation.html.
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
            loop.run_in_executor(executor, _worker_corr, (sym, rows)) for sym, rows in symbol_rows
        ]
        per_symbol: list[dict[str, list[float]]] = await asyncio.gather(*futures)

    # Pool signals across symbols
    pooled: dict[str, list[float]] = defaultdict(list)
    for sym_result in per_symbol:
        for label, sigs in sym_result.items():
            pooled[label].extend(sigs)

    # Filter indicators with insufficient data
    labels = [lbl for lbl, sigs in pooled.items() if len(sigs) >= _MIN_SIGNALS]
    labels.sort()
    n = len(labels)

    print(
        f"\n  Computing {n}x{n} correlation matrix ({n} indicators with >= {_MIN_SIGNALS} signals) ..."
    )

    # Compute pairwise Spearman correlations
    # We need same-length vectors — pad with nan and align by position
    # Since signals come from same bars, lengths should be similar; just truncate to min length per pair
    corr_matrix: dict[str, dict[str, float]] = {lbl: {} for lbl in labels}

    signal_arrays = {lbl: np.array(pooled[lbl]) for lbl in labels}

    for i, lbl_i in enumerate(labels):
        corr_matrix[lbl_i][lbl_i] = 1.0
        for j in range(i + 1, n):
            lbl_j = labels[j]
            arr_i = signal_arrays[lbl_i]
            arr_j = signal_arrays[lbl_j]
            min_len = min(len(arr_i), len(arr_j))
            if min_len < 20:
                corr = float("nan")
            else:
                corr = _spearman(arr_i[:min_len], arr_j[:min_len])
            corr_matrix[lbl_i][lbl_j] = corr
            corr_matrix[lbl_j][lbl_i] = corr

    # Print summary of high-correlation pairs
    print("\n  High-correlation pairs (|corr| > 0.8):")
    high_corr_pairs: list[tuple[float, str, str]] = []
    for i, lbl_i in enumerate(labels):
        for j in range(i + 1, n):
            lbl_j = labels[j]
            c = corr_matrix[lbl_i][lbl_j]
            if np.isfinite(c) and abs(c) > 0.8:
                high_corr_pairs.append((c, lbl_i, lbl_j))
    high_corr_pairs.sort(key=lambda x: -abs(x[0]))
    for c, a, b in high_corr_pairs[:20]:
        print(f"    {a:<22} {b:<22}  corr={c:+.3f}")
    if not high_corr_pairs:
        print("    (none)")

    # Write CSV
    reports_dir = Path(__file__).parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    csv_path = reports_dir / "indicator_correlation_results.csv"

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["indicator"] + labels)
        for lbl_i in labels:
            row = [lbl_i] + [
                f"{corr_matrix[lbl_i].get(lbl_j, float('nan')):.4f}" for lbl_j in labels
            ]
            writer.writerow(row)
    print(f"\n  CSV written to {csv_path}")

    # Write HTML heatmap
    def _cell_color(c: float, is_diag: bool) -> str:
        if is_diag:
            return "#e0e0e0"
        if not np.isfinite(c):
            return "#ffffff"
        ac = abs(c)
        if ac > 0.8:
            return "#ef5350"
        if ac > 0.6:
            return "#ff9800"
        return "#ffffff"

    header_cells = "<th>indicator</th>" + "".join(f"<th>{lbl}</th>" for lbl in labels)
    html_rows = []
    for lbl_i in labels:
        cells = [f"<td><b>{lbl_i}</b></td>"]
        for lbl_j in labels:
            c = corr_matrix[lbl_i].get(lbl_j, float("nan"))
            is_diag = lbl_i == lbl_j
            color = _cell_color(c, is_diag)
            fmt = f"{c:.2f}" if np.isfinite(c) else "nan"
            cells.append(f'<td style="background:{color};text-align:center">{fmt}</td>')
        html_rows.append(f"<tr>{''.join(cells)}</tr>")

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Indicator Correlation Matrix — 15min</title>
<style>
  body {{ font-family: monospace; font-size: 11px; margin: 20px; }}
  h2 {{ margin-bottom: 8px; }}
  table {{ border-collapse: collapse; }}
  th, td {{ border: 1px solid #ccc; padding: 3px 5px; white-space: nowrap; }}
  th {{ background: #e0e0e0; position: sticky; top: 0; }}
  tr:hover {{ outline: 1px solid #999; }}
  .legend {{ margin-top: 12px; }}
  .swatch {{ display: inline-block; width: 16px; height: 16px; vertical-align: middle; margin-right: 4px; }}
</style>
</head>
<body>
<h2>Indicator Pairwise Correlation Matrix — {_INTERVAL} | {_MONTH_START.date()} to {_MONTH_END.date()} | {len(_SYMBOLS)} symbols</h2>
<p>{n} indicators with &ge; {_MIN_SIGNALS} valid signals. {len(high_corr_pairs)} high-correlation pairs (|corr| &gt; 0.8).</p>
<div class="legend">
  <span class="swatch" style="background:#ef5350"></span>|corr| &gt; 0.8 (redundant)&nbsp;&nbsp;
  <span class="swatch" style="background:#ff9800"></span>0.6 &lt; |corr| &le; 0.8 (similar)&nbsp;&nbsp;
  <span class="swatch" style="background:#ffffff;border:1px solid #ccc"></span>|corr| &le; 0.6 (independent)&nbsp;&nbsp;
  <span class="swatch" style="background:#e0e0e0"></span>diagonal
</div>
<br>
<table>
<thead><tr>{header_cells}</tr></thead>
<tbody>
{"".join(html_rows)}
</tbody>
</table>
</body>
</html>
"""
    html_path = reports_dir / "indicator_correlation.html"
    html_path.write_text(html_content, encoding="utf-8")
    print(f"  HTML report written to {html_path}")

    # Soft assertion: we should have computed a matrix with at least some entries
    assert n >= 2, f"Expected at least 2 indicators with enough signals, got {n}"
    finite_corrs = [
        corr_matrix[lbl_i][lbl_j]
        for i, lbl_i in enumerate(labels)
        for j, lbl_j in enumerate(labels)
        if i != j and np.isfinite(corr_matrix[lbl_i][lbl_j])
    ]
    assert len(finite_corrs) > 0, "Expected at least one finite pairwise correlation"
