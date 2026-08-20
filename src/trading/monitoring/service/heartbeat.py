from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from anyio import create_task_group, sleep
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.lifecycle.component import Component
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
        # No cleanup DELETE here: the ingestor and every worker each run their
        # own HeartbeatMonitor against the SAME shared heartbeats table, so a
        # per-process "delete anything not in my own component_names" wipes
        # every OTHER process's rows the moment it starts (and with an empty
        # component_names, as the ingestor uses, it wiped the whole table).
        # Orphaned rows for removed algos are harmless — every read filters
        # explicitly by component_names, so a stale leftover is never queried.
        for name in self._component_names:
            await self._heartbeat.update_heartbeat(name)
        logger.info("HeartbeatMonitor: registered %d components", len(self._component_names))

    async def _run(self) -> None:
        async with create_task_group() as tg:
            tg.start_soon(self._beat_loop)
            tg.start_soon(self._monitor_loop)

    async def _beat_loop(self) -> None:
        # Beat under this instance's own identity — component_names[0] for a
        # worker (must match what _setup/_check_stale watch), falling back to
        # self.name only when component_names is empty (the ingestor's case).
        # Using self.name unconditionally here was the bug: every worker
        # shares the same hardcoded Component name ("heartbeat_monitor"), so
        # all their beats collided on one row while their real, distinctly-
        # named row (seeded once in _setup) was never updated again and
        # always reported stale.
        beat_name = self._component_names[0] if self._component_names else self.name
        consecutive = 0
        while True:
            try:
                await self._heartbeat.update_heartbeat(beat_name)
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
