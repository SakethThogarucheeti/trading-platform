from __future__ import annotations

from trading.core.schemas import SignalEvent, TickEvent
from trading.candles.candle_aggregator import CandleAggregator
from trading.strategy.signal_generator import SignalGenerator
from trading.execution.order_executor import OrderExecutor
from trading.risk.risk_filter import RiskFilter


class AlgoPipeline:
    """Routes one SignalEvent through risk filtering and order execution."""

    def __init__(self, risk_filter: RiskFilter, executor: OrderExecutor) -> None:
        self._risk_filter = risk_filter
        self._executor = executor

    async def run(self, signal: SignalEvent) -> None:
        order = await self._risk_filter.handle(signal)
        if order is not None:
            await self._executor.handle(order)


class TickPipeline:
    """Routes one TickEvent through the full candle → signal → order chain."""

    def __init__(
        self,
        candle_registry: CandleAggregator,
        signal_generator: SignalGenerator,
        algo_pipeline: AlgoPipeline,
    ) -> None:
        self._candle_registry = candle_registry
        self._signal_generator = signal_generator
        self._algo_pipeline = algo_pipeline

    async def run(self, tick: TickEvent) -> None:
        candle = await self._candle_registry.handle(tick)
        if candle is None:
            return
        signals = await self._signal_generator.handle(candle)
        for signal in signals:
            await self._algo_pipeline.run(signal)
