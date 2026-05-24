from __future__ import annotations

from trading.core.messaging import AbstractCircuitBreaker
from trading.core.schemas import SignalEvent
from trading.risk.policy import RiskContext

_CIRCUIT_OPEN = "CIRCUIT_OPEN"


class CircuitBreakerGate:
    """Rejects signals when the market-data circuit breaker is open (stale feed)."""

    def __init__(self, circuit: AbstractCircuitBreaker) -> None:
        self._circuit = circuit

    async def check(self, event: SignalEvent, ctx: RiskContext) -> str | None:
        if self._circuit.is_open():
            return _CIRCUIT_OPEN
        return None
