"""Core report runner — fetch data for a window, then render."""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import UTC, datetime

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from trading.reports.fetch import (
    fetch_algo_configs,
    fetch_audit_logs,
    fetch_decisions,
    fetch_heartbeats,
    fetch_nifty_benchmark,
    fetch_signals,
)
from trading.reports.render import hr, print_strategy_section, print_system_section


async def fetch_report_data(
    start: datetime,
    end: datetime,
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, object]:
    """
    Fetch all live report data for [start, end) and return as a structured dict.

    Used by the dashboard API endpoint /api/reports/live so the React frontend
    can render an interactive version of the terminal report.
    """
    from trading.core.schemas import OrderStatus
    from trading.reports.pnl import compute_pnl

    async with session_factory() as session:
        signals = await fetch_signals(session, start, end)
        decisions = await fetch_decisions(session, start, end)
        audit_logs = await fetch_audit_logs(session, start, end)
        heartbeats = await fetch_heartbeats(session)
        algo_configs = await fetch_algo_configs(session)
        nifty_benchmark = await fetch_nifty_benchmark(session, start, end)

    # Signal funnel
    step_counts: dict[str, int] = defaultdict(int)
    rejection_reasons: dict[str, int] = defaultdict(int)
    for d in decisions:
        step_counts[d.step] += 1
        if d.step == "SIGNAL_REJECTED":
            import json as _json
            ctx: dict[str, object] = {}
            try:
                ctx = _json.loads(d.context) if d.context else {}
            except Exception:
                pass
            rejection_reasons[str(ctx.get("reason", "UNKNOWN"))] += 1

    generated = step_counts.get("SIGNAL_GENERATED", 0)
    accepted = step_counts.get("SIGNAL_ACCEPTED", 0)
    rejected = step_counts.get("SIGNAL_REJECTED", 0)

    # Order funnel
    all_orders = [o for s in signals for o in s.orders]
    by_status: dict[str, int] = defaultdict(int)
    for o in all_orders:
        by_status[o.status] += 1
    total_orders = len(all_orders)
    filled = by_status.get(OrderStatus.FILLED.value, 0)

    # Trades by symbol
    symbol_data: dict[str, dict[str, object]] = defaultdict(
        lambda: {"buys": 0, "sells": 0, "volume": 0, "cash_flow": 0.0}
    )
    for sig in signals:
        for order in sig.orders:
            if order.status != OrderStatus.FILLED.value:
                continue
            d = symbol_data[sig.symbol]
            if sig.side == "BUY":
                d["buys"] = int(d["buys"]) + 1  # type: ignore[arg-type]
                d["cash_flow"] = float(d["cash_flow"]) - order.qty * float(order.avg_price)  # type: ignore[arg-type]
            else:
                d["sells"] = int(d["sells"]) + 1  # type: ignore[arg-type]
                d["cash_flow"] = float(d["cash_flow"]) + order.qty * float(order.avg_price)  # type: ignore[arg-type]
            d["volume"] = int(d["volume"]) + order.qty  # type: ignore[arg-type]

    trades_by_symbol = [
        {"symbol": sym, **data}
        for sym, data in sorted(symbol_data.items())
    ]

    # P&L
    pnl_map = compute_pnl(signals)
    total_gross = sum(float(v["realized"]) for v in pnl_map.values())
    total_costs = sum(float(v["total_costs"]) for v in pnl_map.values())
    total_net = sum(float(v["net_realized"]) for v in pnl_map.values())

    total_capital = sum(
        float(cfg["equity"]) for cfg in algo_configs if cfg.get("enabled")  # type: ignore[arg-type]
    )
    algo_pct = (total_net / total_capital * 100) if total_capital else None

    # Heartbeat status
    now_utc = datetime.now(UTC)
    system_health = []
    for hb in heartbeats:
        last = hb.last_seen
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        system_health.append(
            {
                "module": hb.module,
                "last_seen": last.isoformat(),
                "stale": (now_utc - last).total_seconds() > 300,
            }
        )

    benchmark: dict[str, object] | None = None
    if nifty_benchmark:
        b_pct = nifty_benchmark["pct_return"]
        benchmark = {
            "nifty_open": nifty_benchmark["open"],
            "nifty_close": nifty_benchmark["close"],
            "pct_return": b_pct,
            "algo_pct": algo_pct,
            "alpha": (algo_pct - b_pct) if algo_pct is not None else None,
        }

    return {
        "period": None,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "signal_funnel": {
            "candles_emitted": step_counts.get("CANDLE_EMITTED", 0),
            "signals_generated": generated,
            "signals_accepted": accepted,
            "signals_rejected": rejected,
            "acceptance_rate": accepted / generated if generated else 0.0,
            "rejection_reasons": dict(rejection_reasons),
        },
        "order_funnel": {
            "placed": total_orders,
            "filled": filled,
            "rejected": by_status.get(OrderStatus.REJECTED.value, 0),
            "cancelled": by_status.get(OrderStatus.CANCELLED.value, 0),
            "fill_rate": filled / total_orders if total_orders else 0.0,
        },
        "pnl_summary": {
            "gross": round(total_gross, 2),
            "costs": round(total_costs, 2),
            "net": round(total_net, 2),
            "algo_pct": algo_pct,
        },
        "trades_by_symbol": trades_by_symbol,
        "benchmark": benchmark,
        "algo_configs": algo_configs,
        "system_health": system_health,
    }


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
