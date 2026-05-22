from __future__ import annotations

import asyncio
import logging
import os

from anyio import CancelScope, Event, create_task_group, fail_after, sleep, sleep_forever
from anyio.abc import TaskGroup

from trading.broker.base.broker_stream import BrokerStream
from trading.broker.paper_broker import AbstractPriceStore
from trading.broker.types import Tick
from trading.core.context import thread_id
from trading.core.schemas import TickEvent
from trading.core.types import OnTickCallback
from trading.engine.component import Component
from trading.engine.tick_ingestor import TickIngestor
from trading.engine.tick_publisher import TickPublisher

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECS = 30.0


class KiteIngestor(Component):
    """
    Maintains the broker WebSocket connection and feeds raw ticks into TickIngestor.

    After TickIngestor validates and persists the tick, each registered
    ``on_tick`` callback is called in order. Register the candle→algo→risk→exec
    chain via ``add_on_tick(callback)`` before starting.

    Lifecycle
    ---------
    _setup:   register WS callbacks → connect → wait for on_connect → subscribe tokens
    _run:     inner task group for circuit timer + sleep_forever for tick callbacks
    _teardown: cancel circuit scope → close stream

    Thread safety
    -------------
    The Kite WebSocket fires _on_ws_* callbacks from a background thread. We
    cache the running event loop in _setup() and use call_soon_threadsafe to
    schedule work back onto the anyio event loop from those callbacks.
    The anyio Event and CancelScope are only accessed from the event loop thread.
    """

    def __init__(
        self,
        stream: BrokerStream,
        tick_registry: TickIngestor,
        price_store: AbstractPriceStore | None = None,
        connect_timeout_secs: float = _CONNECT_TIMEOUT_SECS,
        tick_publisher: TickPublisher | None = None,
    ) -> None:
        super().__init__(name="kite_ingestor")
        self._stream = stream
        self._tick_registry = tick_registry
        self._price_store = price_store
        self._connect_timeout_secs = connect_timeout_secs
        self._tick_publisher = tick_publisher
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected: Event | None = None
        self._circuit_scope: CancelScope | None = None
        self._task_group: TaskGroup | None = None
        self._running: bool = False
        self._on_tick_callbacks: list[OnTickCallback] = []

    def add_on_tick(self, callback: OnTickCallback) -> None:
        """Register a downstream callback invoked for every validated tick."""
        self._on_tick_callbacks.append(callback)

    async def reconnect_stream(self) -> None:
        """Close the current WebSocket and reconnect with whatever token is now on the client."""
        if not hasattr(self._stream, "reconnect"):
            return
        self._connected = Event()
        await self._stream.reconnect()  # type: ignore[attr-defined]
        try:
            with fail_after(self._connect_timeout_secs):
                await self._connected.wait()
        except TimeoutError:
            logger.error("KiteIngestor: reconnect timed out")
            return
        tokens = self._tick_registry.get_tokens()
        if tokens:
            await self._stream.subscribe(tokens)
            logger.info("KiteIngestor: reconnected and re-subscribed to %d tokens", len(tokens))

    async def _setup(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._connected = Event()

        self._stream.set_on_connect(self._on_ws_connect)
        self._stream.set_on_ticks(self._on_ws_ticks)
        self._stream.set_on_disconnect(self._on_ws_disconnect)

        await self._stream.connect()
        try:
            with fail_after(self._connect_timeout_secs):
                await self._connected.wait()
        except TimeoutError as err:
            raise RuntimeError(
                f"KiteIngestor: WebSocket did not connect within {self._connect_timeout_secs}s"
            ) from err

        tokens = self._tick_registry.get_tokens()
        if tokens:
            await self._stream.subscribe(tokens)
            logger.info("KiteIngestor: connected and subscribed to %d tokens", len(tokens))
        else:
            logger.warning("KiteIngestor: no instruments configured")
        self._running = True

    async def _run(self) -> None:
        async with create_task_group() as tg:
            self._task_group = tg
            await sleep_forever()
        self._task_group = None

    async def _teardown(self) -> None:
        self._running = False
        self._cancel_circuit_scope()
        await self._stream.close()

    # ------------------------------------------------------------------
    # Circuit breaker management (called from event loop thread only)
    # ------------------------------------------------------------------

    def _cancel_circuit_scope(self) -> None:
        if self._circuit_scope is not None:
            self._circuit_scope.cancel()
            self._circuit_scope = None

    async def _run_circuit_timer(self) -> None:
        with CancelScope() as scope:
            self._circuit_scope = scope
            await sleep(self._tick_registry._circuit_timeout_secs)
        if not scope.cancel_called:
            self._tick_registry.circuit.open()
            if self._tick_publisher is not None and self._task_group is not None:
                self._task_group.start_soon(self._tick_publisher.set_circuit_state, True)
            logger.error(
                "KiteIngestor: circuit OPEN after %.0fs disconnect",
                self._tick_registry._circuit_timeout_secs,
            )
        self._circuit_scope = None

    # ------------------------------------------------------------------
    # Event-loop-side handlers (scheduled via call_soon_threadsafe)
    # ------------------------------------------------------------------

    def _on_connected(self) -> None:
        self._cancel_circuit_scope()
        self._tick_registry.circuit.close()
        if self._connected is not None:
            self._connected.set()
        if self._running and self._tick_publisher is not None and self._task_group is not None:
            self._task_group.start_soon(self._tick_publisher.set_circuit_state, False)
        logger.info("KiteIngestor: WebSocket connected — circuit closed")

    def _schedule_tick(self, raw: Tick) -> None:
        if not self._running:
            return
        tg = self._task_group
        if tg is not None:
            tg.start_soon(self._handle_tick, raw)

    def _schedule_circuit_timer(self) -> None:
        if not self._running:
            return
        tg = self._task_group
        if tg is not None and (self._circuit_scope is None or self._circuit_scope.cancel_called):
            tg.start_soon(self._run_circuit_timer)

    # ------------------------------------------------------------------
    # WebSocket thread callbacks (called from broker's background thread)
    # ------------------------------------------------------------------

    def _on_ws_connect(self) -> None:
        assert self._loop is not None
        self._loop.call_soon_threadsafe(self._on_connected)

    def _on_ws_ticks(self, ticks: list[Tick]) -> None:
        loop = self._loop
        if loop is None:
            return
        for tick in ticks:
            loop.call_soon_threadsafe(self._schedule_tick, tick)

    def _on_ws_disconnect(self, code: int, reason: str) -> None:
        logger.warning("KiteIngestor: disconnected code=%s reason=%r", code, reason)
        assert self._loop is not None
        self._loop.call_soon_threadsafe(self._schedule_circuit_timer)

    async def _handle_tick(self, raw: Tick) -> None:
        thread_id.set(os.urandom(4).hex())

        tick = await self._tick_registry.handle(raw)
        if tick is None:
            return

        # Keep price store current for paper trading fill simulation
        if self._price_store is not None:
            symbol = self._tick_registry.get_symbol(tick.instrument_token) or ""
            if symbol:
                self._price_store.update(symbol, tick.last_price)  # type: ignore[attr-defined]

        if self._tick_publisher is not None:
            await self._tick_publisher.publish(tick)

        for callback in self._on_tick_callbacks:
            try:
                await callback(tick)
            except Exception:
                logger.exception("KiteIngestor: on_tick callback error")
