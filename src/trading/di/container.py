from __future__ import annotations

# Backward-compat shim — canonical home is trading.app.container
from trading.app.container import build_container, build_worker_container

__all__ = ["build_container", "build_worker_container"]
