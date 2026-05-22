from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from anyio import CancelScope, create_task_group, sleep_forever

from trading.core.lifecycle.component import Component

logger = logging.getLogger(__name__)


class AbstractRuntime(ABC):
    """
    Supervisor interface for a set of components.

    Implementations decide the startup ordering, shutdown strategy, and
    restart behaviour.  The default ``Runtime`` starts components in order
    and stops them in reverse; alternative implementations could add
    health-check restarts, dependency graphs, etc.
    """

    @abstractmethod
    async def start(self) -> None:
        """Start all components and block until ``stop()`` is called."""

    @abstractmethod
    def stop(self) -> None:
        """Signal all components to shut down."""

    @property
    @abstractmethod
    def running(self) -> bool:
        """True while the runtime is active."""


class Runtime(AbstractRuntime):
    """
    Supervises a list of components with ordered startup and shutdown.

    Startup
    -------
    Components start in the order provided. Each component's ``_setup()``
    must complete (i.e. ``task_status.started()`` fires) before the next
    component begins its own ``_setup()``. This guarantees dependency-safe
    ordering (e.g. ingestor is RUNNING before candle aggregator subscribes).

    Shutdown
    --------
    Call ``stop()`` from the scheduler or externally — it cancels the
    internal CancelScope, triggering the finally block which stops all
    components in reverse order.
    """

    def __init__(self, components: list[Component]) -> None:
        self._components = components
        self._running = False
        self._cancel_scope: CancelScope | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        """Start all components in order, then block until stop() is called."""
        self._loop = asyncio.get_running_loop()
        self._running = True
        logger.info("Runtime: starting %d components", len(self._components))
        try:
            with CancelScope() as scope:
                self._cancel_scope = scope
                async with create_task_group() as tg:
                    for component in self._components:
                        await tg.start(component.start)
                        logger.info("Runtime: %s is RUNNING", component.name)

                    try:
                        await sleep_forever()
                    finally:
                        logger.info("Runtime: shutting down components")
                        for component in reversed(self._components):
                            try:
                                await component.stop()
                                logger.info("Runtime: %s stopped", component.name)
                            except Exception:
                                logger.exception("Runtime: error stopping %s", component.name)
        finally:
            self._cancel_scope = None
            self._running = False
            logger.info("Runtime: all components stopped")

    def stop(self) -> None:
        """Cancel the running task group, triggering orderly shutdown."""
        if self._cancel_scope is None:
            logger.warning("Runtime: stop() called but runtime is not running")
            return
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._cancel_scope.cancel)
        else:
            self._cancel_scope.cancel()
        logger.info("Runtime: stop requested")

    @property
    def running(self) -> bool:
        return self._running
