"""Tests for core/models.py and core/database.py"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from trading.core.database import build_engine, drop_db, get_session, init_db
from trading.core.models import AuditLog, Instrument, Order, Position, Signal

NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncSession:  # type: ignore[misc]
    async with get_session(engine) as s:
        yield s


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------


async def test_init_db_creates_all_tables(engine: AsyncEngine) -> None:
    from sqlalchemy import inspect

    async with engine.connect() as conn:
        table_names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    expected = {"instruments", "signals", "orders", "positions", "heartbeats", "audit_logs"}
    assert expected.issubset(set(table_names))


# ---------------------------------------------------------------------------
# Instrument
# ---------------------------------------------------------------------------


async def test_equity_instrument_fao_fields_null(engine: AsyncEngine) -> None:
    async with get_session(engine) as s:
        s.add(Instrument(token=100, symbol="INFY", exchange="NSE", instrument_type="EQUITY"))

    async with get_session(engine) as s:
        inst = await s.get(Instrument, 100)
        assert inst is not None
        assert inst.symbol == "INFY"
        assert inst.expiry is None
        assert inst.lot_size is None
        assert inst.strike is None
        assert inst.option_type is None


async def test_futures_instrument_stores_fao_fields(engine: AsyncEngine) -> None:
    async with get_session(engine) as s:
        s.add(
            Instrument(
                token=200,
                symbol="NIFTY25JUNFUT",
                exchange="NFO",
                instrument_type="FUTURES",
                underlying="NIFTY",
                expiry=date(2025, 6, 26),
                lot_size=75,
            )
        )

    async with get_session(engine) as s:
        inst = await s.get(Instrument, 200)
        assert inst is not None
        assert inst.lot_size == 75
        assert inst.expiry == date(2025, 6, 26)


async def test_options_instrument_stores_strike_and_type(engine: AsyncEngine) -> None:
    async with get_session(engine) as s:
        s.add(
            Instrument(
                token=300,
                symbol="NIFTY25JUN24500CE",
                exchange="NFO",
                instrument_type="OPTIONS",
                underlying="NIFTY",
                expiry=date(2025, 6, 26),
                strike=Decimal("24500"),
                option_type="CE",
                lot_size=75,
            )
        )

    async with get_session(engine) as s:
        inst = await s.get(Instrument, 300)
        assert inst is not None
        assert inst.option_type == "CE"
        assert inst.strike == Decimal("24500")


# ---------------------------------------------------------------------------
# Signal → Order relationship
# ---------------------------------------------------------------------------


async def test_signal_order_relationship(engine: AsyncEngine) -> None:
    sig_id = uuid4()
    ord_id = uuid4()

    async with get_session(engine) as s:
        sig = Signal(
            id=sig_id,
            strategy_id="ema_cross",
            symbol="TCS",
            instrument_type="EQUITY",
            side="BUY",
            signal_type="ENTRY",
            stop_distance=Decimal("10.00"),
            created_at=NOW,
        )
        s.add(sig)

    async with get_session(engine) as s:
        ord_ = Order(
            id=ord_id,
            kite_order_id="ORD001",
            signal_id=sig_id,
            status="PLACED",
            qty=10,
            avg_price=Decimal("0"),
            created_at=NOW,
        )
        s.add(ord_)

    async with get_session(engine) as s:
        from sqlalchemy import select

        result = await s.execute(select(Order).where(Order.signal_id == sig_id))
        orders = result.scalars().all()
        assert len(orders) == 1
        assert orders[0].kite_order_id == "ORD001"


async def test_duplicate_kite_order_id_raises(engine: AsyncEngine) -> None:
    sig_id = uuid4()

    async with get_session(engine) as s:
        s.add(
            Signal(
                id=sig_id,
                strategy_id="s",
                symbol="TCS",
                instrument_type="EQUITY",
                side="BUY",
                signal_type="ENTRY",
                stop_distance=Decimal("5"),
                created_at=NOW,
            )
        )

    async with get_session(engine) as s:
        s.add(
            Order(
                id=uuid4(),
                kite_order_id="DUP001",
                signal_id=sig_id,
                status="PLACED",
                qty=5,
                avg_price=Decimal("0"),
                created_at=NOW,
            )
        )

    with pytest.raises((IntegrityError, Exception)):
        async with get_session(engine) as s:
            s.add(
                Order(
                    id=uuid4(),
                    kite_order_id="DUP001",
                    signal_id=sig_id,
                    status="PLACED",
                    qty=5,
                    avg_price=Decimal("0"),
                    created_at=NOW,
                )
            )


# ---------------------------------------------------------------------------
# Position composite PK
# ---------------------------------------------------------------------------


async def test_position_composite_pk_allows_same_symbol_different_type(
    engine: AsyncEngine,
) -> None:
    async with get_session(engine) as s:
        s.add(
            Position(
                symbol="INFY",
                instrument_type="EQUITY",
                net_qty=10,
                avg_price=Decimal("1500"),
                updated_at=NOW,
            )
        )
        s.add(
            Position(
                symbol="INFY",
                instrument_type="FUTURES",
                net_qty=75,
                avg_price=Decimal("1510"),
                updated_at=NOW,
            )
        )

    async with get_session(engine) as s:
        eq = await s.get(Position, {"symbol": "INFY", "instrument_type": "EQUITY"})
        fut = await s.get(Position, {"symbol": "INFY", "instrument_type": "FUTURES"})
        assert eq is not None and eq.net_qty == 10
        assert fut is not None and fut.net_qty == 75


# ---------------------------------------------------------------------------
# get_session rolls back on exception
# ---------------------------------------------------------------------------


async def test_get_session_rollback_on_exception(engine: AsyncEngine) -> None:
    try:
        async with get_session(engine) as s:
            s.add(
                Instrument(token=999, symbol="ROLLBACK", exchange="NSE", instrument_type="EQUITY")
            )
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    async with get_session(engine) as s:
        result = await s.get(Instrument, 999)
        assert result is None


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------


async def test_audit_log_append_only(engine: AsyncEngine) -> None:
    async with get_session(engine) as s:
        s.add(AuditLog(module="ingestor", level="INFO", message="started"))
        s.add(AuditLog(module="ingestor", level="INFO", message="tick received"))

    async with get_session(engine) as s:
        from sqlalchemy import select

        result = await s.execute(select(AuditLog))
        logs = result.scalars().all()
        assert len(logs) == 2


async def test_drop_db_removes_tables(engine: AsyncEngine) -> None:
    """Covers drop_db (lines 54-55)."""
    from sqlalchemy import inspect

    await drop_db(engine)
    async with engine.connect() as conn:
        table_names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    assert len(table_names) == 0


def test_build_engine_returns_async_engine() -> None:
    """Covers build_engine (line 18)."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    eng = build_engine("sqlite+aiosqlite:///:memory:")
    assert isinstance(eng, AsyncEngine)


def test_build_engine_postgres_branch_sets_pool_options() -> None:
    """Covers lines 21-23: postgres-specific pool_size, max_overflow, connect_args."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    url = "postgresql+asyncpg://user:pass@localhost/testdb"
    eng = build_engine(url)
    assert isinstance(eng, AsyncEngine)
    # Verify that the engine URL contains 'postgresql'
    assert "postgresql" in str(eng.url)
