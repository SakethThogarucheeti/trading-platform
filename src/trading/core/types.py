from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from trading.core.schemas import TickEvent

OnTickCallback = Callable[[TickEvent], Coroutine[Any, Any, None]]
