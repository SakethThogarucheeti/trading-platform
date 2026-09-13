from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Coroutine
from typing import Any

from anyio import CancelScope, Event, Lock, create_task_group, fail_after, sleep, sleep_forever

from trading.broker.api import AbstractPriceStore, BrokerStream, Tick
from trading.core.context import thread_id
from trading.core.lifecycle.component import Component
from trading.core.messaging import AbstractCircuitBreaker
from trading.core.schemas import TickEvent
from trading.core.types import OnTickCallback
from trading.tick_ingest.service.ingestor import TickIngestor

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECS = 30.0
_CIRCUIT_TIMEOUT_SECS = 30.0
_RECONNECT_RETRY_INTERVAL_SECS = 60.0


class KiteIngestor(Component):
    """
    Maintains the broker WebSocket connection and feeds raw ticks into TickIngestor.

    After TickIngestor validates and persists the tick, every registered
    ``on_tick`` callback is started concurrently (one per algo's
    candle→algo→risk→exec chain), so a slow or hanging callback can't delay
    the others. Register a chain via ``add_on_tick(callback)`` before starting.

    Lifecycle
    ---------
    _setup:   register WS callbacks → connect → wait for on_connect → subscribe tokens
    _run:     supervisory reconnect loop (self-heals a disconnected/never-connected
              stream — see below) + sleep_forever; the tick-handling work itself
              happens in bridge tasks (see below)
    _teardown: cancel circuit scope → cancel bridge tasks → close stream

    ``_running`` tracks the actual connect/disconnect signal
    ------------------------------------------------------------
    ``_running`` is set True only inside ``_on_connected()`` (the real
    connection-success callback) and False inside ``_on_ws_disconnect()`` — not
    inside whatever ``fail_after`` window ``_setup()``/``reconnect_stream()``
    happened to be waiting in. A WebSocket that connects *after* its caller's
    connect-timeout window already gave up must still flip ``_running`` True;
    otherwise ticks are silently dropped by ``_schedule_tick()`` with a
    "WebSocket connected" log line sitting right there suggesting everything is
    fine (this was a real bug, see trading-platform#41).

    Supervisory reconnect (self-healing)
    -------------------------------------
    Neither ``_setup()``'s initial connect nor ``reconnect_stream()`` (called
    once from the auth callback after login) retry on their own — one timeout
    and the feed stays down until something else intervenes. ``_run()`` starts
    a background loop (``_supervise_connection``) that periodically checks
    ``_running`` and calls ``reconnect_stream()`` again if it's still down,
    covering both entry points' failure to connect with one mechanism.
    ``reconnect_stream()`` is guarded by ``_reconnect_lock`` so the supervisor
    loop and an externally-triggered call (the auth callback) never race.

    Thread safety
    -------------
    The Kite WebSocket fires _on_ws_* callbacks from a background thread. We
    cache the running event loop in _setup() and use call_soon_threadsafe to
    schedule work back onto the event loop from those callbacks.
    The anyio Event and CancelScope are only accessed from the event loop thread.

    A callback run via call_soon_threadsafe executes as a plain event-loop
    callback, not from inside any anyio task's context — anyio's own
    TaskGroup.start_soon() rejects that starting with anyio 4.14 (see #34).
    _schedule_tick/_handle_disconnect therefore spawn their work with
    plain asyncio.Task (via _spawn_bridge_task), tracked in _bridge_tasks and
    cancelled explicitly in _teardown() to preserve the same
    cancel-on-stop behaviour a task group would otherwise give for free.
    """

    def __init__(
        self,
        stream: BrokerStream,
        tick_registry: TickIngestor,
        circuit: AbstractCircuitBreaker,
        circuit_timeout_secs: float = _CIRCUIT_TIMEOUT_SECS,
        price_store: AbstractPriceStore | None = None,
        connect_timeout_secs: float = _CONNECT_TIMEOUT_SECS,
        reconnect_retry_interval_secs: float = _RECONNECT_RETRY_INTERVAL_SECS,
    ) -> None:
        super().__init__(name="kite_ingestor")
        self._stream = stream
        self._tick_registry = tick_registry
        self._circuit = circuit
        self._circuit_timeout_secs = circuit_timeout_secs
        self._price_store = price_store
        self._connect_timeout_secs = connect_timeout_secs
        self._reconnect_retry_interval_secs = reconnect_retry_interval_secs
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected: Event | None = None
        self._circuit_scope: CancelScope | None = None
        self._running: bool = False
        self._reconnect_lock = Lock()
        self._on_tick_callbacks: list[OnTickCallback] = []
        self._bridge_tasks: set[asyncio.Task[None]] = set()

    def add_on_tick(self, callback: OnTickCallback) -> None:
        """Register a downstream callback invoked for every validated tick."""
        self._on_tick_callbacks.append(callback)

    async def reconnect_stream(self) -> None:
        """Close the current WebSocket and reconnect with whatever token is now on the client."""
        if not hasattr(self._stream, "reconnect"):
            return
        async with self._reconnect_lock:
            if self._running:
                # Already reconnected — e.g. a late _on_connected() landed, or a
                # concurrent caller already won the race for this lock.
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
                logger.info(
                    "KiteIngestor: reconnected and re-subscribed to %d tokens", len(tokens)
                )

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
        except TimeoutError:
            # Don't raise: an unauthenticated startup (no Zerodha token yet — the
            # normal state before the daily login completes) would otherwise take
            # down the whole Runtime, including unrelated components. Start
            # disconnected instead; reconnect_stream() (called from the auth
            # callback once login completes) brings the feed up later.
            logger.error(
                "KiteIngestor: WebSocket did not connect within %.0fs — starting "
                "disconnected. Complete Zerodha login to bring the feed up.",
                self._connect_timeout_secs,
            )
            return

        tokens = self._tick_registry.get_tokens()
        if tokens:
            await self._stream.subscribe(tokens)
            logger.info("KiteIngestor: connected and subscribed to %d tokens", len(tokens))
        else:
            logger.warning("KiteIngestor: no instruments configured")

    async def _run(self) -> None:
        async with create_task_group() as tg:
            tg.start_soon(self._supervise_connection)
            await sleep_forever()

    async def _supervise_connection(self) -> None:
        """
        Self-heal a stream that's disconnected (or never connected in the first
        place — see _setup()'s connect-timeout comment) with no other retry path.

        Sleeps a full grace period before each check/attempt, both to avoid
        spamming reconnects and to give a legitimate late _on_connected() (the
        exact race reconnect_stream()'s own fail_after can lose) a chance to
        land on its own first — see the class docstring.
        """
        while True:
            await sleep(self._reconnect_retry_interval_secs)
            if self._running or not hasattr(self._stream, "reconnect"):
                continue
            logger.warning(
                "KiteIngestor: supervisory reconnect — disconnected for over %.0fs",
                self._reconnect_retry_interval_secs,
            )
            try:
                await self.reconnect_stream()
            except Exception:
                logger.exception("KiteIngestor: supervisory reconnect attempt failed")

    async def _teardown(self) -> None:
        self._running = False
        self._cancel_circuit_scope()
        await self._cancel_bridge_tasks()
        await self._stream.close()

    async def _cancel_bridge_tasks(self) -> None:
        if not self._bridge_tasks:
            return
        tasks = list(self._bridge_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _cancel_circuit_scope(self) -> None:
        if self._circuit_scope is not None:
            self._circuit_scope.cancel()
            self._circuit_scope = None

    async def _run_circuit_timer(self) -> None:
        with CancelScope() as scope:
            self._circuit_scope = scope
            await sleep(self._circuit_timeout_secs)
        if not scope.cancel_called:
            self._circuit.open()
            logger.error(
                "KiteIngestor: circuit OPEN after %.0fs disconnect",
                self._circuit_timeout_secs,
            )
        self._circuit_scope = None

    def _on_connected(self) -> None:
        self._running = True
        self._cancel_circuit_scope()
        self._circuit.close()
        if self._connected is not None:
            self._connected.set()
        logger.info("KiteIngestor: WebSocket connected — circuit closed")

    def _schedule_tick(self, raw: Tick) -> None:
        if not self._running:
            return
        self._spawn_bridge_task(self._handle_tick(raw))

    def _handle_disconnect(self) -> None:
        was_running = self._running
        self._running = False
        if was_running and (self._circuit_scope is None or self._circuit_scope.cancel_called):
            self._spawn_bridge_task(self._run_circuit_timer())

    def _spawn_bridge_task(self, coro: Coroutine[Any, Any, None]) -> None:
        """
        Spawn work scheduled from a call_soon_threadsafe callback.

        Deliberately plain asyncio.Task, not TaskGroup.start_soon(): a
        call_soon_threadsafe callback runs outside any anyio task's context,
        and anyio >=4.14 rejects start_soon() from such a context (#34).
        Tracked in _bridge_tasks and cancelled explicitly in _teardown() to
        preserve the cancel-on-stop behaviour a task group gives for free.
        """
        assert self._loop is not None
        task = self._loop.create_task(coro)
        self._bridge_tasks.add(task)
        task.add_done_callback(self._on_bridge_task_done)

    def _on_bridge_task_done(self, task: asyncio.Task[None]) -> None:
        self._bridge_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("KiteIngestor: bridge task failed", exc_info=exc)

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
        self._loop.call_soon_threadsafe(self._handle_disconnect)

    async def _handle_tick(self, raw: Tick) -> None:
        thread_id.set(os.urandom(4).hex())

        tick = await self._tick_registry.handle(raw)
        if tick is None:
            return

        if self._price_store is not None:
            symbol = self._tick_registry.get_symbol(tick.instrument_token) or ""
            if symbol:
                self._price_store.update(symbol, tick.last_price)  # type: ignore[attr-defined]

        async with create_task_group() as tg:
            for callback in self._on_tick_callbacks:
                tg.start_soon(self._run_one_callback, callback, tick)

    async def _run_one_callback(self, callback: OnTickCallback, tick: TickEvent) -> None:
        try:
            await callback(tick)
        except Exception:
            logger.exception("KiteIngestor: on_tick callback error")
