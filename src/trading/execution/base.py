from __future__ import annotations

from abc import ABC, abstractmethod

from trading.core.schemas import ValidatedOrderEvent


class ExecutionEngine(ABC):
    """
    Abstract base for order routing and fill handling.

    Implementations decide how a validated order is sent to the broker
    (market order, TWAP, smart routing, paper simulation, etc.) and how
    fills are processed to update positions.

    The ``OrderExecutor`` component subscribes to the validated_orders Redis
    channel and delegates to an injected ``ExecutionEngine`` implementation.
    This decouples the lifecycle management (Component) from the execution
    logic (pluggable per algo).
    """

    @abstractmethod
    async def execute(self, event: ValidatedOrderEvent) -> None:
        """
        Route a validated order to the broker.

        Implementations must persist the order, call the broker, update order
        status, and publish an ``OrderEvent`` on the ``orders`` channel.
        Paper trading implementations simulate an immediate fill.
        """

    @abstractmethod
    async def handle_fill(
        self,
        kite_order_id: str,
        avg_price: float,
        filled_qty: int,
        symbol: str,
        instrument_type: str,
        side: str,
    ) -> None:
        """
        Process a fill notification.

        Updates order status to FILLED, updates the position atomically,
        and publishes a ``FillEvent`` on the ``fills`` channel.
        Called by either a postback webhook handler or the paper broker simulator.
        """
