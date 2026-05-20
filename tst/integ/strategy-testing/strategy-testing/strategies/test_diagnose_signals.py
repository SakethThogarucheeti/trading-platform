"""
Diagnostic test: run one EMA crossover combo, count trades, then query
audit_logs for rejection details.

Run with:
    cd trading-platform/strategy-testing
    uv run pytest strategy-testing/test_diagnose_signals.py -v -s
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from testing.backtesting.data_loader import FileDataLoader
from testing.backtesting.engine import BacktestSession
from testing.backtesting.report import BacktestConfig, BacktestReport

from trading.config.settings import AlgoSettings

_DATA_DIR = Path(__file__).parents[5] / "data"

pytestmark = pytest.mark.skipif(
    not _DATA_DIR.exists(),
    reason="data/ directory not found — run uv run fetch-data first",
)

_SCHEMA = "bt_diag_9_21_1_5"
_END = datetime(2026, 4, 17, tzinfo=UTC)
_START = _END - timedelta(days=30)
_EQUITY = 10_000.0
_INTERVAL = "15min"
_SYMBOLS = ["INFY", "TCS", "RELIANCE", "HDFCBANK", "ICICIBANK"]


async def test_diagnose_signal_flow(pg_engine, tmp_path):
    """
    Run a backtest and diagnose trade counts vs rejections from audit_logs.
    """
    config = BacktestConfig(
        algo=AlgoSettings(
            name=_SCHEMA,
            instruments=_SYMBOLS,
            strategy_id="ema_crossover",
            candle_intervals=[_INTERVAL],
            equity=_EQUITY,
        ),
        start=_START,
        end=_END,
        loader=FileDataLoader(_DATA_DIR),
        initial_equity=_EQUITY,
        slippage_pct=0.05,
        strategy_params={"fast": 9, "slow": 21, "atr_multiplier": 1.5},
    )

    session = BacktestSession(
        config=config,
        db_engine=pg_engine,
        results_dir=tmp_path,
        db_schema=_SCHEMA,
        keep_schema=True,
    )
    report: BacktestReport = await session.run()

    print(f"\n{'=' * 60}")
    print(f"  Trades: {report.total_trades}  Final equity: {report.final_equity:,.0f}")
    print(f"{'=' * 60}")

    # ------------------------------------------------------------------
    # Read audit_logs from the schema-isolated DB
    # ------------------------------------------------------------------
    async with pg_engine.connect() as conn:
        await conn.execute(text(f'SET search_path TO "{_SCHEMA}"'))

        rows = (
            await conn.execute(
                text("SELECT module, level, message FROM audit_logs ORDER BY created_at")
            )
        ).fetchall()

        print(f"\n--- audit_logs ({len(rows)} rows, rejections only) ---")
        reason_counts: dict[str, int] = defaultdict(int)
        for r in rows:
            if "rejected:" not in r.message:
                continue
            reason = r.message.split("rejected:")[-1].strip()
            reason_counts[reason] += 1
            parts = r.message.split()
            sig_id = parts[1] if len(parts) > 1 else ""
            print(f"  {reason:<24} signal={sig_id[:8]}")

        if reason_counts:
            print("\n--- rejection reason counts ---")
            for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
                print(f"  {reason:<30} {count}")

    # Clean up
    async with pg_engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))

    print(f"{'=' * 60}\n")
