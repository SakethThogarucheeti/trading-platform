"""Tests for core/messaging.py — AbstractRegistry, and engine/tick_ingestor.py — CircuitBreaker"""

from __future__ import annotations

import pytest

from trading.core.messaging import AbstractRegistry
from trading.engine.tick_ingestor import CircuitBreaker

# ---------------------------------------------------------------------------
# AbstractRegistry
# ---------------------------------------------------------------------------


class EchoRegistry(AbstractRegistry):
    """Concrete registry that echoes its input."""

    async def handle(self, event: object) -> object:
        return event


class DoubleRegistry(AbstractRegistry):
    """Concrete registry that returns a transformed value."""

    async def handle(self, event: int) -> int:
        return event * 2


async def test_abstract_registry_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        AbstractRegistry()  # type: ignore[abstract]


async def test_echo_registry_returns_same_event() -> None:
    reg = EchoRegistry()
    result = await reg.handle("hello")
    assert result == "hello"


async def test_double_registry_transforms_event() -> None:
    reg = DoubleRegistry()
    result = await reg.handle(5)
    assert result == 10


async def test_registry_returns_none_when_subclass_returns_none() -> None:
    class NullRegistry(AbstractRegistry):
        async def handle(self, event: object) -> None:
            return None

    reg = NullRegistry()
    assert await reg.handle("anything") is None


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


def test_circuit_starts_closed() -> None:
    cb = CircuitBreaker()
    assert cb.is_open() is False


def test_open_sets_circuit_open() -> None:
    cb = CircuitBreaker()
    cb.open()
    assert cb.is_open() is True


def test_close_clears_open_circuit() -> None:
    cb = CircuitBreaker()
    cb.open()
    cb.close()
    assert cb.is_open() is False


def test_multiple_opens_idempotent() -> None:
    cb = CircuitBreaker()
    cb.open()
    cb.open()
    assert cb.is_open() is True


def test_close_on_already_closed_is_safe() -> None:
    cb = CircuitBreaker()
    cb.close()
    assert cb.is_open() is False


def test_open_close_open_cycle() -> None:
    cb = CircuitBreaker()
    cb.open()
    assert cb.is_open() is True
    cb.close()
    assert cb.is_open() is False
    cb.open()
    assert cb.is_open() is True
