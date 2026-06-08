from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time
from typing import Callable

from pydantic import BaseModel, Field

from trading.core.clock import Clock, SystemClock
from trading.core.messaging import AbstractRegistry
from trading.core.schemas import SignalType
from trading.core.tasks import fire
from trading.risk.api.interfaces import AbstractAuditStore, AbstractPositionStore, AbstractTradingStore, CacherFactory
from trading.risk.api.schemas import ValidatedOrderEvent
from trading.risk.service.policy import RiskContext, RiskGate, RiskSizer
from trading.risk.service.sizer import VolatilitySizer
from trading.strategy.api.schemas import SignalEvent

logger = logging.getLogger(__name__)

_ZERO_QUANTITY = "ZERO_QUANTITY"


@dataclass
class SignalRejectedContext:
    reason: str


@dataclass
class SignalAcceptedContext:
    qty: int
    order_type: str


class RiskConfig(BaseModel):
    equity: float = Field(default=100_000.0, gt=0)
    max_daily_loss_pct: float = Field(default=2.0, gt=0, le=100)
    risk_per_trade_pct: float = Field(default=1.0, gt=0, le=100)
    rc_id: str = "default"
    intraday_cutoff_hour: int = Field(default=15, ge=0, le=23)
    intraday_cutoff_minute: int = Field(default=30, ge=0, le=59)


class RiskFilter(AbstractRegistry):
    """
    Evaluates a SignalEvent against a chain of RiskGates, then sizes the order.
    """

    def __init__(
        self,
        config: RiskConfig,
        gates: list[RiskGate],
        trading: AbstractTradingStore,
        audit: AbstractAuditStore,
        position: AbstractPositionStore,
        factory: CacherFactory,
        clock: Clock | None = None,
        sizer: RiskSizer | None = None,
        equity_provider: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._gates = gates
        self._trading = trading
        self._audit = audit
        self._position = position
        self._factory = factory
        self._clock: Clock = clock or SystemClock()
        self._sizer: RiskSizer = sizer or VolatilitySizer()
        self._equity_provider = equity_provider

    @property
    def config(self) -> RiskConfig:
        return self._config

    async def handle(self, event: SignalEvent) -> ValidatedOrderEvent | None:  # type: ignore[override]
        ctx = await self._build_context(event)

        for gate in self._gates:
            rejection = await gate.check(event, ctx)
            if rejection is not None:
                await self._reject(event, rejection)
                return None

        qty = self._sizer.size(event, ctx)
        if qty == 0:
            await self._reject(event, _ZERO_QUANTITY)
            return None

        try:
            await self._audit.log_audit("risk_filter", "INFO", f"signal {event.signal_id} accepted qty={qty}")
        except Exception:
            pass

        try:
            await self._trading.save_signal(event)
        except Exception:
            logger.warning("RiskFilter: failed to persist signal %s", event.signal_id)

        fire(self._log_decision("SIGNAL_ACCEPTED", event, SignalAcceptedContext(qty=qty, order_type="MARKET")))
        logger.info("RiskFilter: ACCEPTED signal=%s symbol=%s side=%s qty=%d", event.signal_id, event.symbol, event.side.value, qty)
        return ValidatedOrderEvent.from_signal_event(event, qty)

    async def _build_context(self, event: SignalEvent) -> RiskContext:
        now = self._clock.now()
        today = now.date()
        realized_pnl = await self._factory.pnl().get_or_set(  # type: ignore[attr-defined]
            (today,), producer=lambda: self._trading.get_daily_realized_pnl(today)
        )
        position = None
        if event.signal_type == SignalType.ENTRY:
            position = await self._position.get_position(event.symbol, event.instrument_type.value)
        equity = self._equity_provider() if self._equity_provider is not None else self._config.equity
        return RiskContext(
            now=now,
            today=today,
            equity=max(equity, 0.0),
            max_daily_loss_pct=self._config.max_daily_loss_pct,
            risk_per_trade_pct=self._config.risk_per_trade_pct,
            cutoff=time(self._config.intraday_cutoff_hour, self._config.intraday_cutoff_minute),
            realized_pnl=realized_pnl,
            position=position,
        )

    async def _reject(self, event: SignalEvent, reason: str) -> None:
        logger.info("RiskFilter: REJECTED signal=%s reason=%s", event.signal_id, reason)
        fire(self._log_decision("SIGNAL_REJECTED", event, SignalRejectedContext(reason=reason)))
        try:
            await self._audit.log_audit("risk_filter", "WARNING", f"signal {event.signal_id} rejected: {reason}")
        except Exception:
            pass

    async def _log_decision(self, step: str, event: SignalEvent, context: object) -> None:
        if event.tick_log_id <= 0:
            return
        try:
            await self._audit.log_decision(
                step=step, symbol=event.symbol, tick_log_id=event.tick_log_id,
                context=context, signal_id=event.signal_id,
            )
        except Exception:
            logger.exception("RiskFilter: decision log failed for signal %s", event.signal_id)
