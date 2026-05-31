"""Core report runner — fetch data for a window, then render."""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from dotenv import load_dotenv
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from trading.reports.fetch import (
    AlgoConfigSnapshot,
    fetch_algo_configs,
    fetch_audit_logs,
    fetch_decisions,
    fetch_heartbeats,
    fetch_nifty_benchmark,
    fetch_signals,  # used by run_report terminal output only
)
from trading.reports.render import hr, print_strategy_section, print_system_section


@dataclass
class _SymbolRow:
    buys: int = 0
    sells: int = 0
    volume: int = 0
    cash_flow: float = 0.0


class SignalFunnel(BaseModel):
    candles_emitted: int
    signals_generated: int
    signals_accepted: int
    signals_rejected: int
    acceptance_rate: float
    rejection_reasons: dict[str, int]


class OrderFunnel(BaseModel):
    placed: int
    filled: int
    rejected: int
    cancelled: int
    fill_rate: float


class PnlSummary(BaseModel):
    gross: float
    costs: float
    net: float
    algo_pct: float | None


class TradesBySymbol(BaseModel):
    symbol: str
    buys: int
    sells: int
    volume: int
    cash_flow: float


class SystemHealth(BaseModel):
    module: str
    last_seen: str
    stale: bool


class BenchmarkResult(BaseModel):
    nifty_open: float
    nifty_close: float
    pct_return: float
    algo_pct: float | None
    alpha: float | None


class LiveReportData(BaseModel):
    period: str | None
    start: str
    end: str
    signal_funnel: SignalFunnel
    order_funnel: OrderFunnel
    pnl_summary: PnlSummary
    trades_by_symbol: list[TradesBySymbol]
    benchmark: BenchmarkResult | None
    algo_configs: list[AlgoConfigSnapshot]
    system_health: list[SystemHealth]


async def fetch_report_data(
    start: datetime,
    end: datetime,
    session_factory: async_sessionmaker[AsyncSession],
) -> LiveReportData:
    """
    Fetch all live report data for [start, end) and return as a structured dict.

    Used by the dashboard API endpoint /api/reports/live so the React frontend
    can render an interactive version of the terminal report.
    """
    import json as _json

    from trading.reports.trades import fetch_filled_trades, summarize

    async with session_factory() as session:
        decisions = await fetch_decisions(session, start, end)
        await fetch_audit_logs(session, start, end)
        heartbeats = await fetch_heartbeats(session)
        algo_configs = await fetch_algo_configs(session)
        nifty_benchmark = await fetch_nifty_benchmark(session, start, end)

    trades = await fetch_filled_trades(session_factory, start=start, end=end)

    # Signal funnel (from decision log — not from signals table)
    step_counts: dict[str, int] = defaultdict(int)
    rejection_reasons: dict[str, int] = defaultdict(int)
    for d in decisions:
        step_counts[d.step] += 1
        if d.step == "SIGNAL_REJECTED":
            ctx: dict[str, object] = {}
            try:
                ctx = _json.loads(d.context) if d.context else {}
            except Exception:
                pass
            rejection_reasons[str(ctx.get("reason", "UNKNOWN"))] += 1

    generated = step_counts.get("SIGNAL_GENERATED", 0)
    accepted = step_counts.get("SIGNAL_ACCEPTED", 0)
    rejected = step_counts.get("SIGNAL_REJECTED", 0)

    # Order funnel — count from decision log steps
    total_orders = step_counts.get("SIGNAL_ACCEPTED", 0)
    filled = len(trades)

    # Trades by symbol — derived from filled trades
    symbol_rows: dict[str, _SymbolRow] = defaultdict(_SymbolRow)
    for t in trades:
        r = symbol_rows[t.symbol]
        if t.side == "BUY":
            r.buys += 1
            r.cash_flow -= t.qty * t.avg_price
        else:
            r.sells += 1
            r.cash_flow += t.qty * t.avg_price
        r.volume += t.qty

    trades_by_symbol = [
        TradesBySymbol(
            symbol=sym,
            buys=r.buys,
            sells=r.sells,
            volume=r.volume,
            cash_flow=r.cash_flow,
        )
        for sym, r in sorted(symbol_rows.items())
    ]

    # P&L — from TradesQueryService
    pnl = summarize(trades)
    total_gross = pnl.gross
    total_costs = pnl.costs
    total_net = pnl.net

    total_capital = sum(cfg.equity for cfg in algo_configs if cfg.enabled)
    algo_pct = (total_net / total_capital * 100) if total_capital else None

    # Heartbeat status — 5-min threshold for historical reports (longer window than live API)
    _REPORT_STALE_SECS = 300
    now_utc = datetime.now(UTC)
    system_health = []
    for hb in heartbeats:
        last = hb.last_seen
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        system_health.append(SystemHealth(
            module=hb.module,
            last_seen=last.isoformat(),
            stale=(now_utc - last).total_seconds() > _REPORT_STALE_SECS,
        ))

    benchmark: BenchmarkResult | None = None
    if nifty_benchmark:
        b_pct = nifty_benchmark.pct_return
        benchmark = BenchmarkResult(
            nifty_open=nifty_benchmark.open,
            nifty_close=nifty_benchmark.close,
            pct_return=b_pct,
            algo_pct=algo_pct,
            alpha=(algo_pct - b_pct) if algo_pct is not None else None,
        )

    return LiveReportData(
        period=None,
        start=start.isoformat(),
        end=end.isoformat(),
        signal_funnel=SignalFunnel(
            candles_emitted=step_counts.get("CANDLE_EMITTED", 0),
            signals_generated=generated,
            signals_accepted=accepted,
            signals_rejected=rejected,
            acceptance_rate=accepted / generated if generated else 0.0,
            rejection_reasons=dict(rejection_reasons),
        ),
        order_funnel=OrderFunnel(
            placed=total_orders,
            filled=filled,
            rejected=step_counts.get("SIGNAL_REJECTED", 0),
            cancelled=0,
            fill_rate=filled / total_orders if total_orders else 0.0,
        ),
        pnl_summary=PnlSummary(
            gross=round(total_gross, 2),
            costs=round(total_costs, 2),
            net=round(total_net, 2),
            algo_pct=algo_pct,
        ),
        trades_by_symbol=trades_by_symbol,
        benchmark=benchmark,
        algo_configs=algo_configs,
        system_health=system_health,
    )


def _find_db_url() -> str:
    from pathlib import Path

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / ".env"
        if candidate.exists():
            load_dotenv(candidate)
            break

    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL", "")
    if not url:
        sys.exit("ERROR: DATABASE_URL or POSTGRES_URL must be set in .env")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


async def run_report(start: datetime, end: datetime, title: str) -> None:
    """Fetch all data for [start, end) and print the full report."""
    engine = create_async_engine(_find_db_url(), echo=False)

    async with AsyncSession(engine) as session:
        signals = await fetch_signals(session, start, end)
        decisions = await fetch_decisions(session, start, end)
        audit_logs = await fetch_audit_logs(session, start, end)
        heartbeats = await fetch_heartbeats(session)
        algo_configs = await fetch_algo_configs(session)
        nifty_benchmark = await fetch_nifty_benchmark(session, start, end)

    await engine.dispose()

    print()
    hr("═")
    print(f"  {title}")
    hr("═")
    print(f"  Period:    {start.strftime('%Y-%m-%d %H:%M')} – {end.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    print_strategy_section(signals, decisions, algo_configs, nifty_benchmark=nifty_benchmark)
    print_system_section(decisions, audit_logs, heartbeats)

    print()
    hr("═")
    print("  END OF REPORT")
    hr("═")
    print()
