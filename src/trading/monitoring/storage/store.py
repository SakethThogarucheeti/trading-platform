from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.monitoring.storage.models import Heartbeat


class HeartbeatStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def update_heartbeat(self, module: str) -> None:
        # A SELECT-then-INSERT/UPDATE here has a TOCTOU race: the beat loop
        # fires on its own timer without awaiting prior calls, so two
        # overlapping calls for the same module can both see no existing row
        # and both attempt INSERT, raising a duplicate-key error (or one
        # INSERTs while the other's stale UPDATE matches zero rows). INSERT
        # ... ON CONFLICT is atomic at the database level and closes the
        # window entirely — mirrors ConfigStore.upsert_algo_state.
        now = datetime.now(UTC)
        stmt = pg_insert(Heartbeat).values(module=module, last_seen=now)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Heartbeat.module],
            set_={"last_seen": stmt.excluded.last_seen},
        )
        async with self._sf() as session:
            async with session.begin():
                await session.execute(stmt)

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
