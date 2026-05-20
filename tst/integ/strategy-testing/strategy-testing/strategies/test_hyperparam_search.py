"""
EMA Crossover hyperparameter grid search.

Sweeps fast/slow EMA combinations and ATR multipliers over real Zerodha data,
ranks by Sharpe ratio, and prints a sorted results table.

Grid parameters (symbols, periods, ATR multipliers, date range, equity) are
read from ``strategy_config.json`` in the project root — edit that file to
change what the search sweeps without touching test code.

Each completed combo is appended to grid_search_results.csv immediately so
partial progress is preserved if the run is interrupted.

Requires: data/ directory populated via ``uv run fetch-data``
"""

from __future__ import annotations

import asyncio
import csv
import itertools
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from testing.backtesting.data_loader import FileDataLoader
from testing.backtesting.engine import BacktestSession
from testing.backtesting.report import BacktestConfig, BacktestReport

from trading.config.settings import AlgoSettings
from trading.config.strategy_config import load_strategy_config

_DATA_DIR = Path(__file__).parents[5] / "data"

pytestmark = pytest.mark.skipif(
    not _DATA_DIR.exists(),
    reason="data/ directory not found — run uv run fetch-data first",
)

# ---------------------------------------------------------------------------
# Load grid from strategy_config.json
# ---------------------------------------------------------------------------

_CFG = load_strategy_config()
_HP = _CFG.hyperparam_search

_FAST_PERIODS: list[int] = _HP.fast_periods
_SLOW_PERIODS: list[int] = _HP.slow_periods
_ATR_MULTIPLIERS: list[float] = _HP.atr_multipliers

_SYMBOLS: list[str] = _HP.symbols
_INTERVAL: str = _HP.interval
_EQUITY: float = _HP.equity

_END = (
    datetime.fromisoformat(_HP.end_date).replace(tzinfo=UTC)
    if _HP.end_date
    else datetime(2026, 4, 17, tzinfo=UTC)
)
_MONTHS: int | None = _HP.months


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


def _start() -> datetime:
    if _MONTHS is None:
        return datetime(2025, 6, 1, tzinfo=UTC)
    return (_END - timedelta(days=_MONTHS * 30)).replace(hour=0, minute=0, second=0, microsecond=0)


_RESULTS_CSV = Path(__file__).parent / "grid_search_results.csv"
_CSV_FIELDS = [
    "fast",
    "slow",
    "atr_multiplier",
    "sharpe",
    "cagr",
    "max_dd",
    "calmar",
    "win_rate",
    "profit_factor",
    "total_trades",
    "pnl",
    "final_equity",
]


@dataclass
class GridResult:
    fast: int
    slow: int
    atr_multiplier: float
    sharpe: float
    cagr: float
    max_dd: float
    calmar: float
    win_rate: float
    profit_factor: float
    total_trades: int
    pnl: float
    final_equity: float

    def label(self) -> str:
        return f"EMA({self.fast}/{self.slow}) ATRx{self.atr_multiplier}"

    def to_row(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_one(
    fast: int,
    slow: int,
    atr_multiplier: float,
    pg_engine,
    tmp_path,
) -> BacktestReport:
    """Run a single combo in its own DB schema."""
    schema = f"bt_{fast}_{slow}_{str(atr_multiplier).replace('.', '_')}"
    config = BacktestConfig(
        algo=AlgoSettings(
            name=schema,
            instruments=_SYMBOLS,
            strategy_id=_CFG.strategy.id,
            candle_intervals=[_INTERVAL],
            equity=_EQUITY,
        ),
        start=_start(),
        end=_END,
        loader=FileDataLoader(_DATA_DIR),
        initial_equity=_EQUITY,
        slippage_pct=0.05,
        strategy_params={
            **_CFG.strategy.params,
            "fast": fast,
            "slow": slow,
            "atr_multiplier": atr_multiplier,
        },
    )
    session = BacktestSession(
        config=config,
        db_engine=pg_engine,
        results_dir=tmp_path,
        db_schema=schema,
        keep_schema=True,
    )
    return await session.run()


def _append_csv(result: GridResult, csv_path: Path) -> None:
    """Append one result row to the CSV, writing the header only on first call."""
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(result.to_row())


def _print_table(results: list[GridResult]) -> None:
    ranked = sorted(results, key=lambda r: r.sharpe, reverse=True)
    W = 95
    header = (
        f"{'Params':<28} {'Sharpe':>8} {'PnL':>8} {'CAGR':>7} {'MaxDD':>7}"
        f" {'Calmar':>7} {'WinR':>6} {'PF':>6} {'Trades':>7}"
    )
    print(f"\n{'=' * W}")
    print("  EMA Crossover Hyperparameter Grid Search")
    print(f"  Symbols : {', '.join(_SYMBOLS)}")
    print(f"  Period  : {_start().date()} to {_END.date()}")
    print(f"  Interval: {_INTERVAL}   Equity: {_EQUITY:,.0f}")
    print(f"{'=' * W}")
    print(header)
    print("-" * W)
    for r in ranked:
        print(
            f"  {r.label():<26} {r.sharpe:>8.3f} {r.pnl:>+8.0f} {r.cagr:>7.1%}"
            f" {r.max_dd:>7.1%} {r.calmar:>7.2f} {r.win_rate:>6.0%}"
            f" {r.profit_factor:>6.2f} {r.total_trades:>7}"
        )
    print(f"{'=' * W}")
    best = ranked[0]
    print(
        f"  Best by Sharpe: {best.label()}"
        f"  (Sharpe={best.sharpe:.3f}, PnL={best.pnl:+.0f},"
        f" WinR={best.win_rate:.0%}, PF={best.profit_factor:.2f})"
    )
    print(f"{'=' * W}\n")


# ---------------------------------------------------------------------------
# Test: full grid search
# ---------------------------------------------------------------------------


async def test_ema_grid_search(pg_engine, tmp_path):
    """
    Run all fast/slow/ATR combos sequentially — each gets its own Postgres
    schema and Redis channel namespace so they don't interfere.

    Grid parameters are loaded from strategy_config.json in the project root.
    Results are appended to grid_search_results.csv as each combo finishes.
    """
    all_combos = itertools.product(_FAST_PERIODS, _SLOW_PERIODS, _ATR_MULTIPLIERS)
    combos = [(fast, slow, atr_mult) for fast, slow, atr_mult in all_combos if fast < slow]

    _sem = asyncio.Semaphore(1)
    _csv_lock = asyncio.Lock()

    print(f"\n  Running {len(combos)} combos (sequential)", flush=True)
    print(
        f"  Grid  : fast={_FAST_PERIODS}  slow={_SLOW_PERIODS}  atr={_ATR_MULTIPLIERS}",
        flush=True,
    )
    print(f"  Results: {_RESULTS_CSV}", flush=True)

    async def _run_and_record(i: int, fast: int, slow: int, atr_mult: float) -> GridResult:
        async with _sem:
            print(
                f"  [{i}/{len(combos)}] EMA({fast}/{slow}) ATRx{atr_mult} starting...",
                flush=True,
            )
            report = await _run_one(fast, slow, atr_mult, pg_engine, tmp_path)
            result = GridResult(
                fast=fast,
                slow=slow,
                atr_multiplier=atr_mult,
                sharpe=report.sharpe_ratio,
                cagr=report.cagr,
                max_dd=report.max_drawdown,
                calmar=report.calmar_ratio,
                win_rate=report.win_rate,
                profit_factor=report.profit_factor,
                total_trades=report.total_trades,
                pnl=report.final_equity - _EQUITY,
                final_equity=report.final_equity,
            )
            async with _csv_lock:
                _append_csv(result, _RESULTS_CSV)
            print(
                f"  [{i}/{len(combos)}] EMA({fast}/{slow}) ATRx{atr_mult} done"
                f" — sharpe={result.sharpe:.3f}  pnl={result.pnl:+.0f}"
                f"  win%={result.win_rate:.0%}  pf={result.profit_factor:.2f}"
                f"  trades={result.total_trades}",
                flush=True,
            )
            return result

    results: list[GridResult] = await asyncio.gather(
        *[
            _run_and_record(i, fast, slow, atr_mult)
            for i, (fast, slow, atr_mult) in enumerate(combos, 1)
        ]
    )

    _print_table(list(results))

    from sqlalchemy import text

    async with pg_engine.begin() as conn:
        for fast, slow, atr_mult in combos:
            schema = f"bt_{fast}_{slow}_{str(atr_mult).replace('.', '_')}"
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
