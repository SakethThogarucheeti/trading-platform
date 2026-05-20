from __future__ import annotations

import sys
from pathlib import Path

# Make `system_testing` importable — the package dir is named `system-testing`
# (hyphen) which Python can't import directly, so we add its parent to sys.path.
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.core.database import build_session_factory, init_db

# ---------------------------------------------------------------------------
# Testcontainers — real Postgres, scope=session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_container():
    """Start a real Postgres container for the test session."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


# ---------------------------------------------------------------------------
# Per-test fixtures derived from the container
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine(pg_container) -> AsyncEngine:
    """
    Async engine connected to the test Postgres container.

    Tables are created on first use (init_db is idempotent).
    All rows are truncated after each test so tests don't share state.
    """
    url = (
        pg_container.get_connection_url()
        .replace("psycopg2", "asyncpg")
        .replace("postgresql://", "postgresql+asyncpg://")
    )
    eng = create_async_engine(url, echo=False)
    await init_db(eng)
    yield eng
    from sqlalchemy import text

    async with eng.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE orders, signals, positions, instruments, "
                "heartbeats, audit_logs, decision_logs, tick_logs CASCADE"
            )
        )
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    """Async session factory for the test database."""
    return build_session_factory(engine)


@pytest.fixture
def repo():
    """Repository for tests that need DB operations."""
    from trading.storage.repository import Repository

    return Repository()


async def seed_signal(session_factory, event) -> None:
    """
    Insert a Signal row for the given ValidatedOrderEvent's signal_id.

    ExecRegistry requires a Signal row to exist (FK constraint) before it can
    save an Order. In the live pipeline RiskRegistry inserts this row. Tests
    that call ExecRegistry directly must call this helper first.
    """
    from trading.core.schemas import SignalEvent, SignalType
    from trading.storage.repository import Repository

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
