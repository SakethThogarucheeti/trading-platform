"""Tests for execution/base.py — ExecutionEngine ABC"""

from __future__ import annotations

import pytest

from trading.core.schemas import ValidatedOrderEvent
from trading.execution.base import ExecutionEngine


def test_execution_engine_is_abstract() -> None:
    with pytest.raises(TypeError):
        ExecutionEngine()  # type: ignore[abstract]


def test_execution_engine_concrete_subclass_works() -> None:
    class _AlwaysOk(ExecutionEngine):
        async def execute(self, event: ValidatedOrderEvent) -> None:
            pass

        async def handle_fill(
            self, kite_order_id, avg_price, filled_qty, symbol, instrument_type, side
        ) -> None:
            pass

    engine = _AlwaysOk()
    assert isinstance(engine, ExecutionEngine)
