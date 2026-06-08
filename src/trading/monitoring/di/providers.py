from __future__ import annotations

from dishka import Provider, Scope, provide  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.monitoring.storage.store import HeartbeatStore


class MonitoringProvider(Provider):
    scope = Scope.APP

    @provide
    def heartbeat_store(self, sf: async_sessionmaker[AsyncSession]) -> HeartbeatStore:
        return HeartbeatStore(sf)
