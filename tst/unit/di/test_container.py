"""Tests for di/containers/infra.py's build_infra()"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.app.database import init_db
from trading.config.settings import Settings
from trading.di.containers.infra import Infra, build_infra
from trading.execution.storage.store import TradingStore
from trading.tick_ingest.storage.store import AuditStore

# ---------------------------------------------------------------------------
# Fixture -- swaps in an in-memory sqlite engine via build_infra's `engine=`
# override, same intent as the old FakeInfraProvider / .override() calls:
# tests don't need a live Postgres instance. trading-platform#3 removed the
# dependency_injector container hierarchy this used to go through.
# ---------------------------------------------------------------------------


def _fake_settings() -> Settings:
    return Settings(
        zerodha_api_key="test-key",
        zerodha_api_secret="test-secret",
        token_secret_key="test-token-secret",
        postgres_url="postgresql+asyncpg://u:p@localhost/test",  # not used
    )


async def _fake_db_engine() -> AsyncEngine:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    return engine


@pytest.fixture
async def infra() -> AsyncIterator[Infra]:
    engine = await _fake_db_engine()
    built = await build_infra(_fake_settings(), engine=engine)
    try:
        yield built
    finally:
        await built.db_engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_infra_resolves_settings(infra: Infra) -> None:
    assert infra.settings.zerodha_api_key == "test-key"


async def test_infra_resolves_trading_store(infra: Infra) -> None:
    assert isinstance(infra.trading_store, TradingStore)


async def test_infra_resolves_audit_store(infra: Infra) -> None:
    assert isinstance(infra.audit_store, AuditStore)


async def test_infra_resolves_db_engine(infra: Infra) -> None:
    assert infra.db_engine is not None


async def test_infra_resolves_session_factory(infra: Infra) -> None:
    assert callable(infra.session_factory)


async def test_infra_uses_the_overridden_engine(infra: Infra) -> None:
    """The `engine=` override actually takes effect -- build_infra doesn't
    silently fall back to building a real engine from settings.postgres_url
    when one is supplied."""
    assert infra.db_engine.dialect.name == "sqlite"
