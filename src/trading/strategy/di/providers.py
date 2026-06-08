from __future__ import annotations

from dishka import Provider, Scope, provide  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.strategy.storage.store import ChartStore, ConfigStore


class StrategiesProvider(Provider):
    """Wires strategies module storage internals."""

    scope = Scope.APP

    @provide
    def chart_store(self, sf: async_sessionmaker[AsyncSession]) -> ChartStore:
        return ChartStore(sf)

    @provide
    def config_store(self, sf: async_sessionmaker[AsyncSession]) -> ConfigStore:
        return ConfigStore(sf)
