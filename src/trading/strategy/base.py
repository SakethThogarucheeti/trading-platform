from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from trading.core.clock import SYSTEM_CLOCK, Clock
from trading.core.schemas import CandleEvent, InstrumentType, Side, SignalType
from quantindicators.store import AbstractCandleStore

_log = logging.getLogger(__name__)


@dataclass
class RuntimeContext:
    """System-level runtime attributes passed to all strategies at construction time."""

    clock: Clock = field(default_factory=lambda: SYSTEM_CLOCK)


@dataclass
class Signal:
    """
    Trading signal produced by a strategy.

    ``stop_distance`` is used by the risk sizer to compute position size
    (e.g. ATR × multiplier). Must be > 0.

    For backtest reproducibility, pass ``timestamp=candle.timestamp`` explicitly
    when constructing a Signal. The default (``datetime.now(UTC)``) is correct
    for live trading but will vary across runs in backtests.

    ``signal_id`` is auto-generated; every call that returns a Signal produces
    a distinct UUID so the execution layer can deduplicate safely.
    """

    symbol: str
    instrument_type: InstrumentType
    side: Side
    strategy_id: str
    signal_type: SignalType
    stop_distance: float  # always > 0

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    signal_id: UUID = field(default_factory=uuid4)


class Strategy(ABC):
    """
    Abstract base for all signal generators.

    ``on_candle`` MUST be a pure function — no broker calls, no DB writes,
    no network I/O. Side effects belong in the registry layer.
    """

    _chart_cb: Callable[[str, str, float, datetime], None] | None = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        alias = cls.__dict__.get("alias")
        if alias is None:
            return  # abstract intermediates are fine
        if not isinstance(alias, str) or not alias:
            raise TypeError(f"{cls.__name__}.alias must be a non-empty string")

    def set_chart_callback(self, cb: Callable[[str, str, float, datetime], None]) -> None:
        """Injected by AlgoRegistry. Strategies call self.chart() to push indicator values."""
        self._chart_cb = cb

    def chart(
        self,
        chart_name: str,
        series_name: str,
        value: float | None,
        ts: datetime | None = None,
    ) -> None:
        """
        Push an indicator value to the named chart/series.

        Fire-and-forget: the callback schedules a background DB write with no
        added latency on the hot path. None values are silently dropped (warmup).
        """
        if self._chart_cb is not None and value is not None:
            self._chart_cb(chart_name, series_name, value, ts or datetime.now(UTC))

    @property
    def id(self) -> str:
        """Alias of this strategy instance (delegates to the class attribute)."""
        return self.__class__.alias  # type: ignore[attr-defined]

    def set_runtime_context(self, ctx: RuntimeContext) -> None:  # noqa: B027
        """
        Called by create_strategy after construction to supply system-level runtime deps.

        Override to receive the clock or other system attributes. Default is a no-op
        for strategies that have no system-level dependencies.
        """

    def set_store(self, store: AbstractCandleStore) -> None:  # noqa: B027
        """
        Called once by AlgoRegistry before the first on_candle to supply the data store.

        Strategies that use indicators should override this to construct indicator
        instances using the provided store. Default implementation is a no-op for
        strategies that don't use indicators.
        """

    def get_params(self) -> dict[str, object]:
        """Return static strategy configuration (periods, thresholds, etc.)."""
        return {}

    def get_state(self) -> dict[str, object]:
        """
        Return a snapshot of live strategy internals for the monitoring dashboard.

        Override to expose strategy-specific values (e.g. current EMA values).
        Merged into ``algo_state.state`` in Postgres after every candle.
        """
        return {}

    @abstractmethod
    async def on_candle(
        self,
        symbol: str,
        instrument_type: InstrumentType,
        candle: CandleEvent,
    ) -> Signal | None:
        """
        Called on every completed candle.

        Indicator instances (constructed via set_store()) are available here.
        Call ``await self.my_indicator.compute(params)`` to get the current value.

        Returns Signal or None.
        """
