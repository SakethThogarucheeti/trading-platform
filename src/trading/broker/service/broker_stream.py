from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from trading.broker.api.schemas import Tick


class BrokerStream(ABC):
    """
    Abstract base for broker WebSocket streaming feeds.

    Implementations register callbacks before calling connect(), then
    subscribe to instrument tokens. All callbacks are invoked from the
    broker's WebSocket thread; callers are responsible for bridging
    to the asyncio event loop via call_soon_threadsafe / run_coroutine_threadsafe.
    """

    @abstractmethod
    def set_on_connect(self, callback: Callable[[], None]) -> None:
        """Register a callback invoked when the WebSocket connection is established."""

    @abstractmethod
    def set_on_ticks(self, callback: Callable[[list[Tick]], None]) -> None:
        """Register a callback invoked with each batch of tick dicts."""

    @abstractmethod
    def set_on_disconnect(self, callback: Callable[[int, str], None]) -> None:
        """Register a callback invoked when the connection drops (code, reason)."""

    @abstractmethod
    async def connect(self) -> None:
        """Initiate the WebSocket connection."""

    @abstractmethod
    async def subscribe(self, tokens: list[int]) -> None:
        """Subscribe to market data for the given instrument tokens."""

    @abstractmethod
    async def close(self) -> None:
        """Close the WebSocket connection gracefully."""
