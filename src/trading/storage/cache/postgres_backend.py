from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.storage.cache.models import RollingStateCacheRow

logger = logging.getLogger(__name__)


class PostgresKVCache:
    """
    Durable key/value cache backend, implementing the same async get/set/
    delete surface as ValueCache (trading.storage.cache.backend) so it
    drops into BaseCacher subclasses without changing their call sites --
    but wired in only for RollingStateCacher (see di/containers/infra.py).
    trading-platform#79: SignalGenerator's rolling strategy state needs to
    survive a process restart; ApiResponseCacher stays on the in-memory
    ValueCache, since its values are cheap-to-recompute derived data with
    no restart-survival requirement.

    No TTL support -- rows persist until explicitly delete()'d (see
    RollingStateCacher.clear()). set()'s `ttl` param exists only to match
    ValueCache's signature; passing a non-None value is a caller bug and
    logged as a warning rather than silently ignored.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self, key: str) -> Any | None:
        async with self._sf() as session:
            row = await session.get(RollingStateCacheRow, key)
            return json.loads(row.value) if row is not None else None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        if ttl is not None:
            logger.warning(
                "PostgresKVCache.set(%s): ttl=%s has no effect -- this backend is durable "
                "and has no expiry mechanism",
                key,
                ttl,
            )
        raw = json.dumps(value)
        now = datetime.now(UTC)
        stmt = pg_insert(RollingStateCacheRow).values(key=key, value=raw, updated_at=now)
        stmt = stmt.on_conflict_do_update(
            index_elements=[RollingStateCacheRow.key],
            set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
        )
        async with self._sf() as session:
            async with session.begin():
                await session.execute(stmt)

    async def delete(self, key: str) -> None:
        async with self._sf() as session:
            async with session.begin():
                row = await session.get(RollingStateCacheRow, key)
                if row is not None:
                    await session.delete(row)
