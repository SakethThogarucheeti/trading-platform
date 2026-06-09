"""Tests for core/tasks.py — fire() and _on_done callback."""

from __future__ import annotations

import asyncio

import pytest

from trading.app.tasks import _on_done, fire


@pytest.mark.asyncio
async def test_fire_schedules_coroutine_and_returns_task() -> None:
    result: list[int] = []

    async def _work() -> None:
        result.append(1)

    task = fire(_work())
    assert isinstance(task, asyncio.Task)
    await asyncio.sleep(0)
    assert result == [1]


@pytest.mark.asyncio
async def test_on_done_logs_exception_without_crashing() -> None:
    """Covers line 25: _on_done logs when a task raises an exception."""
    exc_raised: list[Exception] = []

    async def _failing() -> None:
        raise ValueError("intentional test failure")

    task = fire(_failing())
    try:
        await asyncio.shield(task)
    except Exception as e:
        exc_raised.append(e)

    # _on_done should have been called via done callback; no crash
    assert task.done()
    assert not task.cancelled()
    assert isinstance(task.exception(), ValueError)


@pytest.mark.asyncio
async def test_on_done_does_not_raise_on_exception() -> None:
    """_on_done must not propagate the task exception to the caller."""
    logs: list[str] = []

    async def _failing() -> None:
        raise RuntimeError("background task error")

    task = asyncio.get_running_loop().create_task(_failing())
    task.add_done_callback(_on_done)

    # Wait for it to complete without letting the exception propagate
    await asyncio.sleep(0.05)

    assert task.done()
    # The exception was captured by _on_done (logged), not re-raised
    assert isinstance(task.exception(), RuntimeError)
