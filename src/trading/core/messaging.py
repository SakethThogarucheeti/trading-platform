from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol

from trading.core.schemas import Side


class FillObserver(Protocol):
    """Receives fill notifications from OrderExecutor."""

    def on_fill(self, side: Side, avg_price: float, qty: int) -> None: ...


class AbstractCircuitBreaker(ABC):
    """
    Interface for circuit breaker state.

    Owned by the tick ingestor; shared by reference with RiskFilter.
    Concrete implementations: CircuitBreaker (in-process) and
    RedisCircuitBreaker (cross-process, worker mode).
    """

    @abstractmethod
    def open(self) -> None:
        """Mark the circuit as open (market data stale)."""

    @abstractmethod
    def close(self) -> None:
        """Mark the circuit as closed (market data healthy)."""

    @abstractmethod
    def is_open(self) -> bool:
        """Return True if the circuit is open."""


class AbstractRegistry(ABC):
    """
    Base class for all pipeline stage registries.

    Each stage (tick, candle, algo, risk, execution) implements its own
    concrete subclass with a typed handle() method. The pipeline file
    calls handle() directly — no channels, no serialisation, no background
    listener tasks.
    """

    @abstractmethod
    async def handle(self, event: Any) -> Any:
        """Process one event and return the result (or None to short-circuit)."""
