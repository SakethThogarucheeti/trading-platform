from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

_log = logging.getLogger(__name__)


def fire(
    coro: Coroutine[Any, Any, object],
    on_error: Callable[[BaseException], None] | None = None,
) -> asyncio.Task[object]:
    """
    Schedule a coroutine as a background task and log any unhandled exception.

    Drop-in for asyncio.get_running_loop().create_task() on the hot tick path
    where the caller must not await the result.

    *on_error* is an optional synchronous callback invoked with the exception
    when the task fails. Use it to route critical audit failures to monitoring
    (e.g. a Telegram alerter) without adding backpressure to the hot path.
    Callers that omit it get the existing log-and-swallow behaviour.
    """
    task = asyncio.get_running_loop().create_task(coro)
    task.add_done_callback(_make_done_callback(on_error))
    return task


def _on_done(task: asyncio.Task[object]) -> None:
    """Default done callback — logs failures, no further routing."""
    if not task.cancelled() and (exc := task.exception()):
        _log.error("background task failed: %s", exc, exc_info=exc)


def _make_done_callback(
    on_error: Callable[[BaseException], None] | None,
) -> Callable[[asyncio.Task[object]], None]:
    if on_error is None:
        return _on_done

    def _on_done_with_hook(task: asyncio.Task[object]) -> None:
        if not task.cancelled() and (exc := task.exception()):
            _log.error("background task failed: %s", exc, exc_info=exc)
            try:
                on_error(exc)
            except Exception:
                _log.debug("fire: on_error callback itself raised", exc_info=True)

    return _on_done_with_hook
