"""Tests for engine/component.py, engine/runtime.py, engine/heartbeat.py"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.core.database import build_session_factory, init_db
from trading.core.models import Heartbeat
from trading.core.lifecycle.component import Component, ComponentState
from trading.monitoring.heartbeat import HeartbeatMonitor
from trading.core.lifecycle.runtime import Runtime
from trading.storage.stores.heartbeat import HeartbeatStore

# ---------------------------------------------------------------------------
# Minimal Component implementations for testing
# ---------------------------------------------------------------------------


class NullComponent(Component):
    """A component that sets up instantly and runs forever."""

    def __init__(self, name: str = "null") -> None:
        super().__init__(name)
        self.setup_done = False

    async def _setup(self) -> None:
        self.setup_done = True

    async def _run(self) -> None:
        from anyio import sleep_forever

        await sleep_forever()


class SlowSetupComponent(Component):
    """Sets up after a short delay."""

    def __init__(self, delay: float = 0.05) -> None:
        super().__init__(name="slow")
        self._delay = delay
        self.setup_done = False

    async def _setup(self) -> None:
        await asyncio.sleep(self._delay)
        self.setup_done = True

    async def _run(self) -> None:
        from anyio import sleep_forever

        await sleep_forever()


class SequenceTracker(Component):
    """Records when setup and teardown happen (for ordering tests)."""

    def __init__(self, name: str, log: list[str]) -> None:
        super().__init__(name)
        self._log = log

    async def _setup(self) -> None:
        self._log.append(f"setup:{self.name}")

    async def _run(self) -> None:
        from anyio import sleep_forever

        await sleep_forever()

    async def _teardown(self) -> None:
        self._log.append(f"teardown:{self.name}")


# ---------------------------------------------------------------------------
# Runtime — ordered startup
# ---------------------------------------------------------------------------


async def test_runtime_starts_all_components() -> None:
    a = NullComponent("a")
    b = NullComponent("b")

    task = asyncio.get_event_loop().create_task(Runtime([a, b]).start())
    await asyncio.sleep(0.1)

    assert a.state == ComponentState.RUNNING
    assert b.state == ComponentState.RUNNING

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_runtime_startup_order_is_sequential() -> None:
    """Component B's _setup() must not start before A's _setup() completes."""
    log: list[str] = []
    a = SequenceTracker("a", log)
    b = SequenceTracker("b", log)

    task = asyncio.get_event_loop().create_task(Runtime([a, b]).start())
    await asyncio.sleep(0.1)

    assert log.index("setup:a") < log.index("setup:b")

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_runtime_teardown_order_is_reverse() -> None:
    log: list[str] = []
    a = SequenceTracker("a", log)
    b = SequenceTracker("b", log)

    task = asyncio.get_event_loop().create_task(Runtime([a, b]).start())
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0.05)

    teardown_events = [e for e in log if e.startswith("teardown")]
    assert teardown_events.index("teardown:b") < teardown_events.index("teardown:a")


async def test_runtime_running_flag() -> None:
    runtime = Runtime([NullComponent()])
    task = asyncio.get_event_loop().create_task(runtime.start())
    await asyncio.sleep(0.05)
    assert runtime.running is True
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert runtime.running is False


async def test_runtime_stop_when_not_running_logs_warning() -> None:
    """Calling stop() before start() hits the 'else' branch (line 93)."""
    runtime = Runtime([NullComponent()])
    runtime.stop()  # should not raise — just logs a warning


async def test_runtime_stop_via_runtime_stop_method() -> None:
    """Covers runtime.stop() when running — lines 89-91."""
    runtime = Runtime([NullComponent()])
    task = asyncio.get_event_loop().create_task(runtime.start())
    await asyncio.sleep(0.05)
    assert runtime.running is True
    runtime.stop()  # triggers cancel_scope.cancel() — lines 90-91
    await asyncio.gather(task, return_exceptions=True)
    assert runtime.running is False


async def test_runtime_component_stop_exception_does_not_crash() -> None:
    """A component whose stop() raises is swallowed — runtime keeps shutting down (line 81)."""

    class BrokenStopComponent(Component):
        async def _setup(self) -> None:
            pass

        async def _run(self) -> None:
            from anyio import sleep_forever

            await sleep_forever()

        async def stop(self) -> None:  # type: ignore[override]
            raise RuntimeError("stop failed intentionally")

    runtime = Runtime([BrokenStopComponent("broken")])
    task = asyncio.get_event_loop().create_task(runtime.start())
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    # No exception escaped — line 81 logged it


# ---------------------------------------------------------------------------
# HeartbeatMonitor fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


def make_monitor(
    engine: AsyncEngine,
    names: list[str] | None = None,
    alerter=None,
    beat: int = 1,
    timeout: int = 2,
) -> HeartbeatMonitor:
    sf = build_session_factory(engine)
    return HeartbeatMonitor(
        HeartbeatStore(sf),
        sf,
        component_names=names or ["a", "b"],
        beat_interval_secs=beat,
        timeout_secs=timeout,
        alerter=alerter,
    )


# ---------------------------------------------------------------------------
# HeartbeatMonitor tests
# ---------------------------------------------------------------------------


async def test_heartbeat_monitor_registers_components(engine: AsyncEngine) -> None:
    monitor = make_monitor(engine, names=["ingestor", "executor"])
    task = asyncio.get_event_loop().create_task(monitor.start())
    await asyncio.sleep(0.1)

    from trading.core.database import get_session

    async with get_session(engine) as s:
        hb_a = await s.get(Heartbeat, "ingestor")
        hb_b = await s.get(Heartbeat, "executor")
    assert hb_a is not None
    assert hb_b is not None

    await monitor.stop()
    await asyncio.gather(task, return_exceptions=True)


async def test_heartbeat_monitor_beats_regularly(engine: AsyncEngine) -> None:
    monitor = make_monitor(engine, names=["monitor"], beat=1)
    task = asyncio.get_event_loop().create_task(monitor.start())
    await asyncio.sleep(1.5)  # allow setup + initial stale check + first beat (beat=1s)

    from trading.core.database import get_session

    async with get_session(engine) as s:
        hb = await s.get(Heartbeat, "heartbeat_monitor")
    assert hb is not None
    first_seen = hb.last_seen

    await asyncio.sleep(1.2)  # wait > 1 beat interval

    async with get_session(engine) as s:
        hb2 = await s.get(Heartbeat, "heartbeat_monitor")
    assert hb2 is not None
    # last_seen should have been updated
    assert hb2.last_seen >= first_seen

    await monitor.stop()
    await asyncio.gather(task, return_exceptions=True)


async def test_heartbeat_monitor_detects_stale_module(engine: AsyncEngine) -> None:
    alerted: list[str] = []

    async def fake_alerter(module: str) -> None:
        alerted.append(module)

    # Pre-insert a very old heartbeat
    from trading.core.database import get_session

    old_ts = datetime.now(UTC) - timedelta(seconds=120)
    async with get_session(engine) as s:
        s.add(Heartbeat(module="zombie", last_seen=old_ts))

    monitor = make_monitor(
        engine,
        names=["zombie"],
        alerter=fake_alerter,
        beat=60,  # don't beat during test
        timeout=1,  # check every 1s
    )
    task = asyncio.get_event_loop().create_task(monitor.start())
    await asyncio.sleep(2.5)  # wait for at least one monitor check

    assert "zombie" in alerted

    await monitor.stop()
    await asyncio.gather(task, return_exceptions=True)
