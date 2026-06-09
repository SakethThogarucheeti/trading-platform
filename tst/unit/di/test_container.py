"""Tests for di/providers.py and di/container.py"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from dishka import AsyncContainer, Provider, Scope, provide
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from trading.config.settings import Settings
from trading.app.database import build_session_factory, init_db
from trading.app.container import build_container
from trading.tick_ingest.storage.store import AuditStore
from trading.execution.storage.store import TradingStore

# ---------------------------------------------------------------------------
# Test infrastructure provider — replaces prod InfraProvider in tests
# ---------------------------------------------------------------------------


class FakeInfraProvider(Provider):
    """Swaps prod infra (Postgres) for in-memory equivalents."""

    scope = Scope.APP

    @provide
    def settings(self) -> Settings:
        return Settings(
            zerodha_api_key="test-key",
            zerodha_api_secret="test-secret",
            postgres_url="postgresql+asyncpg://u:p@localhost/test",  # not used
        )

    @provide
    async def db_engine(self) -> AsyncIterator[AsyncEngine]:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        await init_db(engine)
        yield engine
        await engine.dispose()

    @provide
    def session_factory(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return build_session_factory(engine)

    @provide
    def trading_store(self, sf: async_sessionmaker[AsyncSession]) -> TradingStore:
        return TradingStore(sf)

    @provide
    def audit_store(self, sf: async_sessionmaker[AsyncSession]) -> AuditStore:
        return AuditStore(sf)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def container() -> AsyncIterator[AsyncContainer]:  # type: ignore[misc]
    async with build_container(FakeInfraProvider()) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_container_resolves_settings(container: AsyncContainer) -> None:
    settings = await container.get(Settings)
    assert settings.zerodha_api_key == "test-key"


async def test_container_resolves_trading_store(container: AsyncContainer) -> None:
    store = await container.get(TradingStore)
    assert isinstance(store, TradingStore)


async def test_container_resolves_audit_store(container: AsyncContainer) -> None:
    store = await container.get(AuditStore)
    assert isinstance(store, AuditStore)


async def test_container_resolves_db_engine(container: AsyncContainer) -> None:
    engine = await container.get(AsyncEngine)
    assert engine is not None


async def test_container_resolves_session_factory(container: AsyncContainer) -> None:
    factory = await container.get(async_sessionmaker[AsyncSession])
    assert callable(factory)


async def test_trading_store_singleton(container: AsyncContainer) -> None:
    store1 = await container.get(TradingStore)
    store2 = await container.get(TradingStore)
    assert store1 is store2


async def test_extra_provider_overrides_default(container: AsyncContainer) -> None:
    """TestInfraProvider's settings override InfrastructureProvider's settings."""
    settings = await container.get(Settings)
    assert settings.zerodha_api_key == "test-key"
