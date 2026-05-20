"""DB fetch helpers shared by all report periods."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from trading.core.models import AlgoConfig, AuditLog, Candle, DecisionLog, Heartbeat, Signal

logger = logging.getLogger(__name__)

_NIFTY_SYMBOL = "NIFTY 50"


def _safe_json(s: str | None) -> dict[str, object]:
    if not s:
        return {}
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        logger.warning("reports: malformed JSON: %r", s[:100])
        return {}


async def fetch_signals(session: AsyncSession, start: datetime, end: datetime) -> list[Signal]:
    result = await session.execute(
        select(Signal)
        .where(Signal.created_at >= start, Signal.created_at < end)
        .options(selectinload(Signal.orders))
        .order_by(Signal.created_at)
    )
    return list(result.scalars().all())


async def fetch_decisions(
    session: AsyncSession, start: datetime, end: datetime
) -> list[DecisionLog]:
    result = await session.execute(
        select(DecisionLog)
        .where(DecisionLog.created_at >= start, DecisionLog.created_at < end)
        .order_by(DecisionLog.created_at)
    )
    return list(result.scalars().all())


async def fetch_audit_logs(session: AsyncSession, start: datetime, end: datetime) -> list[AuditLog]:
    result = await session.execute(
        select(AuditLog)
        .where(AuditLog.created_at >= start, AuditLog.created_at < end)
        .order_by(AuditLog.created_at)
    )
    return list(result.scalars().all())


async def fetch_heartbeats(session: AsyncSession) -> list[Heartbeat]:
    """Current heartbeat snapshot — not windowed by date."""
    result = await session.execute(select(Heartbeat).order_by(Heartbeat.module))
    return list(result.scalars().all())


async def fetch_nifty_benchmark(
    session: AsyncSession, start: datetime, end: datetime
) -> dict[str, float] | None:
    """
    Fetch Nifty 50 open/close for the period to compute buy-and-hold return.

    Returns a dict with open, close, and pct_return, or None if data is absent.
    The symbol queried is "NIFTY 50" (Zerodha's NSE index symbol).
    """
    result = await session.execute(
        select(Candle)
        .where(
            Candle.symbol == _NIFTY_SYMBOL,
            Candle.ts >= start,
            Candle.ts < end,
        )
        .order_by(Candle.ts)
    )
    candles = result.scalars().all()
    if not candles:
        return None

    open_price = float(candles[0].open)
    close_price = float(candles[-1].close)
    if open_price == 0:
        return None
    pct_return = (close_price - open_price) / open_price * 100
    return {"open": open_price, "close": close_price, "pct_return": pct_return}


async def fetch_algo_configs(session: AsyncSession) -> list[dict[str, object]]:
    """Current algo config + state snapshot — not windowed by date."""
    result = await session.execute(select(AlgoConfig).options(selectinload(AlgoConfig.state)))
    configs = result.scalars().all()
    out: list[dict[str, object]] = []
    for cfg in configs:
        state = _safe_json(cfg.state.state if cfg.state else None)
        out.append(
            {
                "name": cfg.name,
                "strategy_id": cfg.strategy_id,
                "equity": cfg.equity,
                "enabled": cfg.enabled,
                "params": _safe_json(cfg.params),
                "warmup_candles": cfg.warmup_candles,
                "state": state,
            }
        )
    return out
