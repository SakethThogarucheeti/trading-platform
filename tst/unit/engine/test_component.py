"""
Tests for engine/component.py

Each test exercises one defined behaviour of the Component base class.
Concrete subclasses (SpyComponent, FailingSetupComponent, etc.) are defined
inline — they are test scaffolding, not production code.
"""

from __future__ import annotations

import pytest
from anyio import create_task_group, sleep, sleep_forever

from trading.engine.component import Component, ComponentState

# ---------------------------------------------------------------------------
# Concrete test doubles
# ---------------------------------------------------------------------------


class SpyComponent(Component):
    """Records lifecycle events so tests can assert on call order and state."""

    def __init__(self, name: str = "spy") -> None:
        super().__init__(name)
        self.setup_called = False
        self.run_called = False
        self.teardown_called = False
        self.setup_state_snapshot: ComponentState | None = None
        self.run_state_snapshot: ComponentState | None = None

    async def _setup(self) -> None:
        self.setup_called = True
        self.setup_state_snapshot = self.state

    async def _run(self) -> None:
        self.run_called = True
        self.run_state_snapshot = self.state
        await sleep_forever()

    async def _teardown(self) -> None:
        self.teardown_called = True


class FailingSetupComponent(Component):
    """Raises in _setup to verify startup abort behaviour."""

    async def _setup(self) -> None:
        raise RuntimeError("setup failed")

    async def _run(self) -> None:  # pragma: no cover
        await sleep_forever()


class FailingRunComponent(Component):
    """Raises in _run after signalling ready."""

    async def _setup(self) -> None:
        pass

    async def _run(self) -> None:
        raise RuntimeError("run failed")


class SlowTeardownComponent(Component):
    """Simulates a component that takes time to clean up."""

    def __init__(self) -> None:
        super().__init__("slow")
        self.teardown_called = False

    async def _setup(self) -> None:
        pass

    async def _run(self) -> None:
        await sleep_forever()

    async def _teardown(self) -> None:
        await sleep(0.05)
        self.teardown_called = True


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def start_then_stop(component: Component, run_for: float = 0.05) -> None:
    """Start a component in a TaskGroup, let it run briefly, then stop it."""
    async with create_task_group() as tg:
        await tg.start(component.start)
        await sleep(run_for)
        await component.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_initial_state_is_created() -> None:
    component = SpyComponent()
    assert component.state is ComponentState.CREATED


async def test_setup_called_before_running() -> None:
    component = SpyComponent()
    await start_then_stop(component)
    assert component.setup_called
    # _setup ran while state was STARTING
    assert component.setup_state_snapshot is ComponentState.STARTING


async def test_state_is_running_after_setup() -> None:
    component = SpyComponent()
    await start_then_stop(component)
    # _run saw RUNNING
    assert component.run_state_snapshot is ComponentState.RUNNING


async def test_run_called() -> None:
    component = SpyComponent()
    await start_then_stop(component)
    assert component.run_called


async def test_teardown_called_after_stop() -> None:
    component = SpyComponent()
    await start_then_stop(component)
    assert component.teardown_called


async def test_state_is_stopped_after_stop() -> None:
    component = SpyComponent()
    await start_then_stop(component)
    assert component.state is ComponentState.STOPPED


async def test_tg_start_blocks_until_setup_complete() -> None:
    """
    tg.start(component.start) must not return until _setup() has finished.
    A second component started after the first should always see the first
    as RUNNING, never STARTING.
    """
    first = SpyComponent("first")
    second = SpyComponent("second")
    snapshots: list[ComponentState] = []

    class ObservingComponent(Component):
        async def _setup(self) -> None:
            snapshots.append(first.state)

        async def _run(self) -> None:
            await sleep_forever()

    second = ObservingComponent("second")

    async with create_task_group() as tg:
        await tg.start(first.start)
        await tg.start(second.start)
        await first.stop()
        await second.stop()

    assert snapshots == [ComponentState.RUNNING]


async def test_stop_before_start_transitions_to_stopped() -> None:
    component = SpyComponent()
    await component.stop()
    assert component.state is ComponentState.STOPPED


async def test_failing_setup_raises_and_does_not_reach_running() -> None:
    # anyio wraps task exceptions in ExceptionGroup
    component = FailingSetupComponent("failing")
    with pytest.raises(BaseExceptionGroup) as exc_info:
        async with create_task_group() as tg:
            await tg.start(component.start)
    causes = [e for e in exc_info.value.exceptions]
    assert any(isinstance(e, RuntimeError) and "setup failed" in str(e) for e in causes)
    assert component.state is not ComponentState.RUNNING


async def test_failing_run_still_calls_teardown() -> None:
    """Even if _run() raises, _teardown() must be called."""

    class TeardownSpy(FailingRunComponent):
        def __init__(self) -> None:
            super().__init__("failing_run")
            self.teardown_called = False

        async def _teardown(self) -> None:
            self.teardown_called = True

    component = TeardownSpy()
    with pytest.raises(BaseExceptionGroup):
        async with create_task_group() as tg:
            await tg.start(component.start)

    assert component.teardown_called
    assert component.state is ComponentState.STOPPED


async def test_slow_teardown_completes_before_stop_returns() -> None:
    component = SlowTeardownComponent()
    await start_then_stop(component)
    # stop() awaits _done, which is set only after _teardown() finishes
    assert component.teardown_called


async def test_repr_contains_name_and_state() -> None:
    component = SpyComponent("my_component")
    r = repr(component)
    assert "my_component" in r
    assert "CREATED" in r


async def test_state_transitions_in_order() -> None:
    """Record every state the component passes through."""
    states: list[ComponentState] = []

    class RecordingComponent(Component):
        async def _setup(self) -> None:
            states.append(self.state)

        async def _run(self) -> None:
            states.append(self.state)
            await sleep_forever()

        async def _teardown(self) -> None:
            states.append(self.state)  # still RUNNING at teardown entry, then STOPPED

    component = RecordingComponent("recording")

    async with create_task_group() as tg:
        await tg.start(component.start)
        await component.stop()

    # _setup → STARTING, _run → RUNNING, _teardown → RUNNING (before finally sets STOPPED)
    assert states[0] is ComponentState.STARTING
    assert states[1] is ComponentState.RUNNING
    # final state is STOPPED
    assert component.state is ComponentState.STOPPED
