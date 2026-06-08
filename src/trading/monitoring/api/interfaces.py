from __future__ import annotations

from typing import Protocol


class AbstractHeartbeatStore(Protocol):
    async def update_heartbeat(self, module: str) -> None: ...

    async def get_stale_modules(self, timeout_secs: int, modules: list[str]) -> list[str]: ...


class AbstractAlerter(Protocol):
    async def send_alert(self, message: str, event_type: str = "") -> None: ...
