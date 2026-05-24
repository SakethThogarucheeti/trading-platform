"""Tests for core/pipeline.py — AlgoPipeline and TickPipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading.core.pipeline import AlgoPipeline, TickPipeline
from trading.core.schemas import (
    InstrumentType,
    OrderType,
    Side,
    SignalEvent,
    SignalType,
    TickEvent,
    ValidatedOrderEvent,
)
from uuid import uuid4


def _signal() -> SignalEvent:
    return SignalEvent(
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        strategy_id="test",
        signal_type=SignalType.ENTRY,
        stop_distance=1.0,
        tick_log_id=1,
    )


def _validated_order() -> ValidatedOrderEvent:
    return ValidatedOrderEvent(
        signal_id=uuid4(),
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        tick_log_id=1,
    )


def _tick() -> TickEvent:
    return TickEvent(
        instrument_token=738561,
        last_price=1500.0,
        volume=1000,
        instrument_type=InstrumentType.EQUITY,
        timestamp=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        tick_log_id=1,
    )


# ---------------------------------------------------------------------------
# AlgoPipeline
# ---------------------------------------------------------------------------


class TestAlgoPipeline:
    @pytest.mark.anyio
    async def test_run_calls_risk_then_executor_when_order_produced(self) -> None:
        order = _validated_order()
        risk = MagicMock()
        risk.handle = AsyncMock(return_value=order)
        executor = MagicMock()
        executor.handle = AsyncMock()

        pipe = AlgoPipeline(risk_filter=risk, executor=executor)
        await pipe.run([_signal()])

        risk.handle.assert_awaited_once()
        executor.handle.assert_awaited_once_with(order)

    @pytest.mark.anyio
    async def test_run_skips_executor_when_risk_rejects(self) -> None:
        risk = MagicMock()
        risk.handle = AsyncMock(return_value=None)
        executor = MagicMock()
        executor.handle = AsyncMock()

        pipe = AlgoPipeline(risk_filter=risk, executor=executor)
        await pipe.run([_signal()])

        risk.handle.assert_awaited_once()
        executor.handle.assert_not_called()


# ---------------------------------------------------------------------------
# TickPipeline
# ---------------------------------------------------------------------------


class TestTickPipeline:
    @pytest.mark.anyio
    async def test_run_calls_candle_then_signal_then_algo(self) -> None:
        from trading.core.schemas import CandleEvent

        candle = CandleEvent(
            symbol="INFY",
            instrument_type=InstrumentType.EQUITY,
            interval="5min",
            open=1500.0,
            high=1510.0,
            low=1490.0,
            close=1505.0,
            volume=10000,
            timestamp=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
            tick_log_id=1,
        )
        signal = _signal()

        candle_agg = MagicMock()
        candle_agg.handle = AsyncMock(return_value=candle)
        sig_gen = MagicMock()
        sig_gen.handle = AsyncMock(return_value=[signal])
        algo_pipe = MagicMock()
        algo_pipe.run = AsyncMock()

        pipe = TickPipeline(
            candle_registry=candle_agg,
            signal_generator=sig_gen,
            algo_pipeline=algo_pipe,
        )
        await pipe.run(_tick())

        candle_agg.handle.assert_awaited_once()
        sig_gen.handle.assert_awaited_once_with(candle)
        algo_pipe.run.assert_awaited_once_with([signal])

    @pytest.mark.anyio
    async def test_run_stops_when_candle_not_complete(self) -> None:
        candle_agg = MagicMock()
        candle_agg.handle = AsyncMock(return_value=None)
        sig_gen = MagicMock()
        sig_gen.handle = AsyncMock()
        algo_pipe = MagicMock()
        algo_pipe.run = AsyncMock()

        pipe = TickPipeline(
            candle_registry=candle_agg,
            signal_generator=sig_gen,
            algo_pipeline=algo_pipe,
        )
        await pipe.run(_tick())

        sig_gen.handle.assert_not_called()
        algo_pipe.run.assert_not_called()

    @pytest.mark.anyio
    async def test_run_handles_multiple_signals(self) -> None:
        from trading.core.schemas import CandleEvent

        candle = CandleEvent(
            symbol="INFY",
            instrument_type=InstrumentType.EQUITY,
            interval="5min",
            open=1500.0, high=1510.0, low=1490.0, close=1505.0,
            volume=10000,
            timestamp=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
            tick_log_id=1,
        )
        signals = [_signal(), _signal()]

        candle_agg = MagicMock()
        candle_agg.handle = AsyncMock(return_value=candle)
        sig_gen = MagicMock()
        sig_gen.handle = AsyncMock(return_value=signals)
        algo_pipe = MagicMock()
        algo_pipe.run = AsyncMock()

        pipe = TickPipeline(
            candle_registry=candle_agg,
            signal_generator=sig_gen,
            algo_pipeline=algo_pipe,
        )
        await pipe.run(_tick())

        algo_pipe.run.assert_awaited_once_with(signals)
