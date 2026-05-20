"""Shared test helpers for system tests."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from trading.core.schemas import SignalEvent, SignalType, ValidatedOrderEvent
from trading.storage.repository import Repository


async def seed_signal(session_factory: async_sessionmaker, event: ValidatedOrderEvent) -> None:
    """
    Insert a Signal row for a ValidatedOrderEvent's signal_id.

    ExecRegistry has a FK constraint: orders.signal_id → signals.id.
    In the live pipeline RiskRegistry inserts this row before returning the
    ValidatedOrderEvent. Tests that call ExecRegistry directly must call this first.
    """
    sig = SignalEvent(
        signal_id=event.signal_id,
        symbol=event.symbol,
        instrument_type=event.instrument_type,
        side=event.side,
        strategy_id="test",
        signal_type=SignalType.ENTRY,
        stop_distance=1.0,
        tick_log_id=0,
    )
    repo = Repository()
    async with session_factory() as session:
        async with session.begin():
            await repo.save_signal(session, sig)
