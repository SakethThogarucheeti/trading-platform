from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from quantindicators.store import AbstractCandleStore

from trading.candles.api.schemas import CandleEvent
from trading.core.clock import SYSTEM_CLOCK, Clock
from trading.core.schemas import InstrumentType, Side, SignalType

_log = logging.getLogger(__name__)


@dataclass
class RuntimeContext:
    """System-level runtime attributes passed to all strategies at construction time."""

    clock: Clock = field(default_factory=lambda: SYSTEM_CLOCK)


@dataclass
class Signal:
    symbol: str
    instrument_type: InstrumentType
    side: Side
    strategy_id: str
    signal_type: SignalType
    stop_distance: float
    entry_price: float = 0.0

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    signal_id: UUID = field(default_factory=uuid4)


class AlgoRunConfig(BaseModel):
    instrument_strategy_map: dict[str, str]
    equity: float = Field(default=100_000.0, gt=0)
    warmup_candles: int = Field(default=200, gt=0)
    algo_name: str = "default"
    instrument_types: dict[str, str] = Field(default_factory=dict)
    session_id: str | None = None


@dataclass
class AlgoInstance:
    strategy: Strategy
    instrument_type: InstrumentType
    interval: str = ""
    bars_seen: int = 0
    warmed_up: bool = False
    last_signal_at: str | None = None

    def tick_bar(self, interval: str, warmup_candles: int) -> None:
        self.interval = interval
        self.bars_seen += 1
        if self.bars_seen >= warmup_candles:
            self.warmed_up = True

    def record_signal(self, now: datetime) -> None:
        self.last_signal_at = now.isoformat()

    def is_ready(self) -> bool:
        return self.strategy._store is not None

    def state_dict(self, warmup_candles: int) -> dict[str, object]:
        return {
            "bars_seen": self.bars_seen,
            "warmup_candles": warmup_candles,
            "warmup_complete": self.warmed_up,
            "bars_remaining": 0 if self.warmed_up else max(0, warmup_candles - self.bars_seen),
            "last_signal_at": self.last_signal_at,
            **self.strategy.get_state(),
        }


class Strategy(ABC):
    """
    Abstract base for all signal generators.

    ``on_candle`` MUST be a pure function — no broker calls, no DB writes,
    no network I/O. Side effects belong in the registry layer.
    """

    alias: ClassVar[str]
    _store: AbstractCandleStore | None = None
    _chart_cb: Callable[[str, str, float, datetime], None] | None = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        alias = cls.__dict__.get("alias")
        if alias is None:
            return
        if not isinstance(alias, str) or not alias:
            raise TypeError(f"{cls.__name__}.alias must be a non-empty string")

    def set_chart_callback(self, cb: Callable[[str, str, float, datetime], None]) -> None:
        self._chart_cb = cb

    def chart(
        self,
        chart_name: str,
        series_name: str,
        value: float | None,
        ts: datetime | None = None,
    ) -> None:
        if self._chart_cb is not None and value is not None:
            self._chart_cb(chart_name, series_name, value, ts or datetime.now(UTC))

    @property
    def id(self) -> str:
        return self.__class__.alias

    def set_runtime_context(self, ctx: RuntimeContext) -> None:  # noqa: B027
        pass

    def set_store(self, store: AbstractCandleStore) -> None:  # noqa: B027
        pass

    def warmup(self, symbol: str, candles: list[CandleEvent]) -> None:  # noqa: B027
        pass

    def get_params(self) -> dict[str, object]:
        return {}

    def get_state(self) -> dict[str, object]:
        return {}

    def rolling_state(self) -> dict[str, object]:
        return {}

    async def restore_from_state(self, state: dict[str, object]) -> bool:
        return True

    @abstractmethod
    async def on_candle(
        self,
        symbol: str,
        instrument_type: InstrumentType,
        candle: CandleEvent,
    ) -> Signal | None: ...
