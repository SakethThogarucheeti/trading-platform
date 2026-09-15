from __future__ import annotations

from typing import Protocol


class AbstractHeartbeatStore(Protocol):
    async def update_heartbeat(self, module: str) -> None: ...

    async def get_stale_modules(self, timeout_secs: int, modules: list[str]) -> list[str]: ...


class AbstractFailedDispatchStore(Protocol):
    async def record(self, site: str, payload: dict[str, object], error: str) -> None: ...
