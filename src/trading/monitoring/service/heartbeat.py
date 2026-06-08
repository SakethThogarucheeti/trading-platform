from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from anyio import create_task_group, sleep
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.lifecycle.component import Component
from trading.core.models import Heartbeat
from trading.monitoring.api.interfaces import AbstractHeartbeatStore

logger = logging.getLogger(__name__)


class HeartbeatMonitor(Component):
    def __init__(
        self,
        heartbeat: AbstractHeartbeatStore,
        session_factory: async_sessionmaker[AsyncSession],
        component_names: list[str],
        beat_interval_secs: int = 5,
        timeout_secs: int = 15,
        alerter: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(name="heartbeat_monitor")
        self._heartbeat = heartbeat
        self._session_factory = session_factory
        self._component_names = component_names
        self._beat_interval = beat_interval_secs
        self._timeout = timeout_secs
        self._alerter = alerter

    async def _setup(self) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                if self._component_names:
                    await session.execute(
                        delete(Heartbeat).where(Heartbeat.module.not_in(self._component_names))
                    )
                else:
                    await session.execute(delete(Heartbeat))
        for name in self._component_names:
            await self._heartbeat.update_heartbeat(name)
        logger.info("HeartbeatMonitor: registered %d components", len(self._component_names))

    async def _run(self) -> None:
        async with create_task_group() as tg:
            tg.start_soon(self._beat_loop)
            tg.start_soon(self._monitor_loop)

    async def _beat_loop(self) -> None:
        consecutive = 0
        while True:
            try:
                await self._heartbeat.update_heartbeat(self.name)
                consecutive = 0
            except Exception:
                consecutive += 1
                logger.exception("HeartbeatMonitor: beat failed (%d/3)", consecutive)
                if consecutive >= 3:
                    raise
            await sleep(self._beat_interval)

    async def _monitor_loop(self) -> None:
        await self._check_stale()
        while True:
            await sleep(self._timeout)
            await self._check_stale()

    async def _check_stale(self) -> None:
        try:
            stale = await self._heartbeat.get_stale_modules(self._timeout, modules=self._component_names)
            for module in stale:
                logger.warning("HeartbeatMonitor: %s is stale", module)
                if self._alerter is not None:
                    await self._alerter(module)
        except Exception:
            logger.exception("HeartbeatMonitor: monitor check failed")
