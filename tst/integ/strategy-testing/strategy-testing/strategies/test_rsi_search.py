"""
RSI Mean-Reversion hyperparameter grid search.

Sweeps oversold/overbought thresholds and ATR multipliers.
Grid parameters are read from strategy_config.json (grids.rsi_mean_reversion).

Each completed combo is appended to rsi_search_results.csv immediately.

Requires: data/ directory populated via ``uv run fetch-data``
"""

from __future__ import annotations

import asyncio
import csv
import itertools
import json as _json
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
# Load config
# ---------------------------------------------------------------------------

_CFG = load_strategy_config()
_HP = _CFG.hyperparam_search
_STRAT = _CFG.strategies.get("rsi_mean_reversion")
_GRID = (
    _CFG.hyperparam_search.grid
    if _HP.active_strategy == "rsi_mean_reversion"
    else (
        _CFG.hyperparam_search.grid  # fall back to whatever grid is loaded
    )
)
# Always read the RSI-specific grid regardless of active_strategy setting
_RSI_GRID = (
    load_strategy_config().hyperparam_search.grid
    if _HP.active_strategy == "rsi_mean_reversion"
    else {}
)

# Read RSI grid directly from raw config
_RAW = _json.loads((Path(__file__).parents[5] / "strategy_config.json").read_text())
_RSI_GRID = _RAW["hyperparam_search"]["grids"]["rsi_mean_reversion"]

_OVERSOLD_LEVELS: list[float] = _RSI_GRID.get("oversold_levels", [30])
_OVERBOUGHT_LEVELS: list[float] = _RSI_GRID.get("overbought_levels", [70])
_ATR_MULTIPLIERS: list[float] = _RSI_GRID.get("atr_multipliers", [1.5])
_SYMBOLS: list[str] = _HP.symbols
_INTERVAL: str = _HP.interval
_EQUITY: float = _HP.equity
_END = (
    datetime.fromisoformat(_HP.end_date).replace(tzinfo=UTC)
    if _HP.end_date
    else datetime(2026, 4, 17, tzinfo=UTC)
)
_MONTHS: int | None = _HP.months

_RESULTS_CSV = Path(__file__).parent / "rsi_search_results.csv"
_CSV_FIELDS = [
    "oversold",
    "overbought",
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


def _start() -> datetime:
    if _MONTHS is None:
        return datetime(2025, 6, 1, tzinfo=UTC)
    return (_END - timedelta(days=_MONTHS * 30)).replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass
class GridResult:
    oversold: float
    overbought: float
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
        return f"RSI({self.oversold:.0f}/{self.overbought:.0f}) ATRx{self.atr_multiplier}"

    def to_row(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


async def _run_one(
    oversold: float, overbought: float, atr_mult: float, pg_engine, tmp_path
) -> BacktestReport:
    schema = f"bt_rsi_{int(oversold)}_{int(overbought)}_{str(atr_mult).replace('.', '_')}"
    config = BacktestConfig(
        algo=AlgoSettings(
            name=schema,
            instruments=_SYMBOLS,
            strategy_id="rsi_mean_reversion",
            candle_intervals=[_INTERVAL],
            equity=_EQUITY,
        ),
        start=_start(),
        end=_END,
        loader=FileDataLoader(_DATA_DIR),
        initial_equity=_EQUITY,
        slippage_pct=0.05,
        strategy_params={
            "oversold": oversold,
            "overbought": overbought,
            "atr_multiplier": atr_mult,
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


def _append_csv(result: GridResult) -> None:
    write_header = not _RESULTS_CSV.exists()
    with _RESULTS_CSV.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(result.to_row())


def _print_table(results: list[GridResult]) -> None:
    ranked = sorted(results, key=lambda r: r.sharpe, reverse=True)
    W = 95
    header = (
        f"{'Params':<30} {'Sharpe':>8} {'PnL':>8} {'CAGR':>7} {'MaxDD':>7}"
        f" {'Calmar':>7} {'WinR':>6} {'PF':>6} {'Trades':>7}"
    )
    print(f"\n{'=' * W}")
    print("  RSI Mean-Reversion Hyperparameter Grid Search")
    print(f"  Symbols : {', '.join(_SYMBOLS)}")
    print(
        f"  Period  : {_start().date()} to {_END.date()}"
        f"  Interval: {_INTERVAL}  Equity: {_EQUITY:,.0f}"
    )
    print(f"{'=' * W}")
    print(header)
    print("-" * W)
    for r in ranked:
        print(
            f"  {r.label():<28} {r.sharpe:>8.3f} {r.pnl:>+8.0f} {r.cagr:>7.1%}"
            f" {r.max_dd:>7.1%} {r.calmar:>7.2f} {r.win_rate:>6.0%}"
            f" {r.profit_factor:>6.2f} {r.total_trades:>7}"
        )
    print(f"{'=' * W}")
    best = ranked[0]
    print(
        f"  Best: {best.label()}  Sharpe={best.sharpe:.3f}  PnL={best.pnl:+.0f}"
        f"  WinR={best.win_rate:.0%}  PF={best.profit_factor:.2f}"
    )
    print(f"{'=' * W}\n")


async def test_rsi_grid_search(pg_engine, tmp_path):
    """RSI mean-reversion grid search — reads grid from strategy_config.json."""
    all_combos = itertools.product(_OVERSOLD_LEVELS, _OVERBOUGHT_LEVELS, _ATR_MULTIPLIERS)
    combos = [(os_, ob, atr) for os_, ob, atr in all_combos if os_ < ob]

    _sem = asyncio.Semaphore(1)
    _csv_lock = asyncio.Lock()

    print(f"\n  Running {len(combos)} RSI combos (sequential)", flush=True)
    print(
        f"  Grid  : oversold={_OVERSOLD_LEVELS}"
        f"  overbought={_OVERBOUGHT_LEVELS}  atr={_ATR_MULTIPLIERS}",
        flush=True,
    )

    async def _run_and_record(i: int, os_: float, ob: float, atr: float) -> GridResult:
        async with _sem:
            print(
                f"  [{i}/{len(combos)}] RSI({os_:.0f}/{ob:.0f}) ATRx{atr} starting...",
                flush=True,
            )
            report = await _run_one(os_, ob, atr, pg_engine, tmp_path)
            result = GridResult(
                oversold=os_,
                overbought=ob,
                atr_multiplier=atr,
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
                _append_csv(result)
            print(
                f"  [{i}/{len(combos)}] RSI({os_:.0f}/{ob:.0f}) ATRx{atr} done"
                f" — sharpe={result.sharpe:.3f}  pnl={result.pnl:+.0f}"
                f"  win%={result.win_rate:.0%}  trades={result.total_trades}",
                flush=True,
            )
            return result

    results: list[GridResult] = await asyncio.gather(
        *[_run_and_record(i, os_, ob, atr) for i, (os_, ob, atr) in enumerate(combos, 1)]
    )
    _print_table(list(results))

    from sqlalchemy import text

    async with pg_engine.begin() as conn:
        for os_, ob, atr in combos:
            schema = f"bt_rsi_{int(os_)}_{int(ob)}_{str(atr).replace('.', '_')}"
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
