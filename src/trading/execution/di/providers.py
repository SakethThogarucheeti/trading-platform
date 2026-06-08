from __future__ import annotations

from dishka import Provider, Scope, provide  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.execution.storage.store import PositionStore


class ExecutionProvider(Provider):
    """Wires execution module storage internals."""

    scope = Scope.APP

    @provide
    def position_store(self, sf: async_sessionmaker[AsyncSession]) -> PositionStore:
        return PositionStore(sf)
