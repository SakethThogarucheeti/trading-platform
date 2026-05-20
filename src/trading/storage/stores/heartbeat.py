from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.models import Heartbeat


class AbstractHeartbeatStore(ABC):
    @abstractmethod
    async def update_heartbeat(self, module: str) -> None: ...

    @abstractmethod
    async def get_stale_modules(
        self, timeout_secs: int, modules: list[str] | None = None
    ) -> list[str]: ...


class HeartbeatStore(AbstractHeartbeatStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def update_heartbeat(self, module: str) -> None:
        now = datetime.now(UTC)
        async with self._sf() as session:
            async with session.begin():
                existing = await session.get(Heartbeat, module)
                if existing is None:
                    session.add(Heartbeat(module=module, last_seen=now))
                else:
                    existing.last_seen = now

    async def get_stale_modules(
        self, timeout_secs: int, modules: list[str] | None = None
    ) -> list[str]:
        cutoff = datetime.now(UTC) - timedelta(seconds=timeout_secs)
        async with self._sf() as session:
            stmt = select(Heartbeat)
            if modules is not None:
                stmt = stmt.where(Heartbeat.module.in_(modules))
            result = await session.execute(stmt)
            heartbeats = result.scalars().all()

        stale: list[str] = []
        for hb in heartbeats:
            last_seen = hb.last_seen
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=UTC)
            if last_seen < cutoff:
                stale.append(hb.module)
        return stale
