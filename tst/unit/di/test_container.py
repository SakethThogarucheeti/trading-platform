"""Tests for di/containers/app.py"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from dependency_injector import providers
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.app.database import init_db
from trading.config.settings import Settings
from trading.di.containers.app import AppContainer
from trading.execution.storage.store import TradingStore
from trading.tick_ingest.storage.store import AuditStore

# ---------------------------------------------------------------------------
# Fixture -- overrides infra with in-memory equivalents, same intent as the
# old FakeInfraProvider: swap prod infra (Postgres) for a real sqlite engine
# so tests don't need a live Postgres instance.
# ---------------------------------------------------------------------------


def _fake_settings() -> Settings:
    return Settings(
        zerodha_api_key="test-key",
        zerodha_api_secret="test-secret",
        token_secret_key="test-token-secret",
        postgres_url="postgresql+asyncpg://u:p@localhost/test",  # not used
    )


async def _fake_db_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield engine
    await engine.dispose()


@pytest.fixture
async def container() -> AsyncIterator[AppContainer]:  # type: ignore[misc]
    c = AppContainer()
    c.infra.settings.override(providers.Object(_fake_settings()))
    c.infra.db_engine.override(providers.Resource(_fake_db_engine))

    await c.init_resources()
    try:
        yield c
    finally:
        await c.shutdown_resources()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_container_resolves_settings(container: AppContainer) -> None:
    settings = container.infra.settings()
    assert settings.zerodha_api_key == "test-key"


async def test_container_resolves_trading_store(container: AppContainer) -> None:
    store = await container.infra.trading_store()
    assert isinstance(store, TradingStore)


async def test_container_resolves_audit_store(container: AppContainer) -> None:
    store = await container.infra.audit_store()
    assert isinstance(store, AuditStore)


async def test_container_resolves_db_engine(container: AppContainer) -> None:
    engine = await container.infra.db_engine()
    assert engine is not None


async def test_container_resolves_session_factory(container: AppContainer) -> None:
    factory = await container.infra.session_factory()
    assert callable(factory)


async def test_trading_store_singleton(container: AppContainer) -> None:
    store1 = await container.infra.trading_store()
    store2 = await container.infra.trading_store()
    assert store1 is store2


async def test_provider_override_replaces_default(container: AppContainer) -> None:
    """Overriding a provider replaces what the container resolves for it."""
    settings = container.infra.settings()
    assert settings.zerodha_api_key == "test-key"
