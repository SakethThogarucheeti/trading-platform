from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


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
