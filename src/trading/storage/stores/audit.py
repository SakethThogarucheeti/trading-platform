from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID

from trading.tick_ingest.storage.store import AuditStore, AuditContext


@dataclass
class AuditContext(AuditContext):
    pass


class AbstractAuditStore(ABC):
    @abstractmethod
    async def log_tick(self, event: object, symbol: str) -> int: ...

    @abstractmethod
    async def log_decision(self, step: str, symbol: str, tick_log_id: int, context: object, algo_name: str | None = None, signal_id: UUID | None = None, session_id: str | None = None) -> None: ...

    @abstractmethod
    async def log_audit(self, module: str, level: str, message: str) -> None: ...


__all__ = ["AuditContext", "AbstractAuditStore", "AuditStore"]
