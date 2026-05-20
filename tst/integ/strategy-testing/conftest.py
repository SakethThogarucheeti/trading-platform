"""
Root conftest.py for strategy-testing.

Adds the strategy-testing package root to sys.path so that both the
``testing`` library and ``strategy-testing`` tests can import each other
without installing the package first.

Provides a session-scoped Postgres fixture (via testcontainers) for tests
that run a full BacktestSession.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.core.database import init_db

# Add strategy-testing/ to path so `import testing` resolves
sys.path.insert(0, str(Path(__file__).parent))


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
# Per-test fixtures derived from the containers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pg_engine(pg_container) -> AsyncEngine:
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
