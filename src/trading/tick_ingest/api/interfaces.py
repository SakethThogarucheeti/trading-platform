from __future__ import annotations

from typing import Protocol

from trading.broker.api import BrokerStream, Tick  # noqa: F401 — re-exported for consumers
from trading.tick_ingest.api.schemas import TickEvent


class AbstractAuditStore(Protocol):
    """Storage contract for tick_ingest — persists raw tick records."""

    async def log_tick(self, event: TickEvent, symbol: str) -> int:
        """Persist a tick and return its DB-assigned tick_log_id."""
        ...
