from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

_log = logging.getLogger(__name__)


def fire(coro: Coroutine[Any, Any, object]) -> asyncio.Task[object]:
    """
    Schedule a coroutine as a background task and log any unhandled exception.

    Drop-in for asyncio.get_running_loop().create_task() on the hot tick path
    where the caller must not await the result.
    """
    task = asyncio.get_running_loop().create_task(coro)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: asyncio.Task[object]) -> None:
    if not task.cancelled() and (exc := task.exception()):
        _log.error("background task failed: %s", exc, exc_info=exc)
