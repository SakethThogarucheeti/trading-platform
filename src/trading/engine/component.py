from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from anyio import TASK_STATUS_IGNORED, CancelScope, Event
from anyio.abc import TaskStatus


class ComponentState(Enum):
    CREATED = "CREATED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


class Component(ABC):
    """
    Base class for all long-running trading system components.

    Lifecycle
    ---------
    CREATED → STARTING (_setup runs) → RUNNING (_run blocks) → STOPPING → STOPPED

    Ordered startup with anyio TaskGroup
    -------------------------------------
    tg.start(component.start) blocks until _setup() completes and signals ready.
    The next component's _setup() only begins after the previous one is RUNNING,
    guaranteeing dependency-safe startup order.

    Example
    -------
        async with create_task_group() as tg:
            await tg.start(ingestor.start)      # WebSocket connected before…
            await tg.start(aggregator.start)    # …candle aggregator subscribes
            await sleep_forever()
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.state: ComponentState = ComponentState.CREATED
        self._cancel_scope: CancelScope | None = None
        self._done: Event | None = None

    async def start(self, *, task_status: TaskStatus[None] = TASK_STATUS_IGNORED) -> None:
        """
        Start the component.

        Calls _setup(), signals ready via task_status.started(), then blocks
        inside _run() until stop() is called. Calls _teardown() in a finally
        block so cleanup always runs, even if _run() raises.
        """
        self._done = Event()
        self.state = ComponentState.STARTING
        await self._setup()
        self.state = ComponentState.RUNNING
        task_status.started()
        try:
            with CancelScope() as scope:
                self._cancel_scope = scope
                await self._run()
        finally:
            await self._teardown()
            self.state = ComponentState.STOPPED
            self._done.set()

    async def stop(self) -> None:
        """
        Stop the component and wait until it has fully shut down.

        Cancels the internal CancelScope so _run() returns, then waits for
        _teardown() to complete and the state to reach STOPPED. Safe to call
        before start() — in that case it transitions directly to STOPPED.
        """
        self.state = ComponentState.STOPPING
        if self._cancel_scope is not None:
            self._cancel_scope.cancel()
        if self._done is not None:
            await self._done.wait()
        else:
            # start() was never called — nothing to wait for
            self.state = ComponentState.STOPPED

    @abstractmethod
    async def _setup(self) -> None:
        """
        One-time initialisation. Completes before the component signals ready.

        Examples: connect WebSocket, fetch warm-up data, load DB state.
        Raise here to abort startup — the exception propagates out of tg.start().
        """

    @abstractmethod
    async def _run(self) -> None:
        """
        Main loop. Runs until stop() cancels the internal scope.

        Typically ends with await sleep_forever() for event-driven components
        (tick callbacks, Redis subscription handlers) or an explicit loop for
        polling components.
        """

    async def _teardown(self) -> None:
        """
        Optional cleanup. Called after _run() exits, before state → STOPPED.

        Override to close connections, flush buffers, or release resources.
        Default is a no-op.
        """
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, state={self.state.value})"
