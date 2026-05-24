from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import time

from pydantic import BaseModel, Field

from trading.core.clock import Clock, SystemClock
from trading.core.messaging import AbstractRegistry
from trading.core.schemas import (
    SignalEvent,
    SignalType,
    ValidatedOrderEvent,
)
from trading.core.tasks import fire
from trading.risk.policy import RiskContext, RiskGate, RiskSizer
from trading.risk.sizer import VolatilitySizer
from trading.storage.cache import CacherFactory
from trading.storage.stores.audit import AbstractAuditStore, AuditContext
from trading.storage.stores.position import AbstractPositionStore
from trading.storage.stores.trading import AbstractTradingStore

logger = logging.getLogger(__name__)

_ZERO_QUANTITY = "ZERO_QUANTITY"


@dataclass
class SignalRejectedContext(AuditContext):
    reason: str


@dataclass
class SignalAcceptedContext(AuditContext):
    qty: int
    order_type: str


class RiskConfig(BaseModel):
    """Configuration for the risk evaluation stage."""

    equity: float = Field(default=100_000.0, gt=0)
    max_daily_loss_pct: float = Field(default=2.0, gt=0, le=100)
    risk_per_trade_pct: float = Field(default=1.0, gt=0, le=100)
    rc_id: str = "default"
    intraday_cutoff_hour: int = Field(default=15, ge=0, le=23)
    intraday_cutoff_minute: int = Field(default=30, ge=0, le=59)


class RiskFilter(AbstractRegistry):
    """
    Evaluates a SignalEvent against a chain of RiskGates, then sizes the order.

    Gates run in order; the first rejection short-circuits. If all gates pass,
    the RiskSizer computes the quantity. A zero quantity is also a rejection.

    Returns a ValidatedOrderEvent if the signal passes, None if rejected.
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
    ) -> None:
        self._config = config
        self._gates = gates
        self._trading = trading
        self._audit = audit
        self._position = position
        self._factory = factory
        self._clock: Clock = clock or SystemClock()
        self._sizer: RiskSizer = sizer or VolatilitySizer()

    @property
    def config(self) -> RiskConfig:
        return self._config

    # ------------------------------------------------------------------
    # AbstractRegistry
    # ------------------------------------------------------------------

    async def handle(self, event: SignalEvent) -> ValidatedOrderEvent | None:
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
            await self._audit.log_audit(
                "risk_filter",
                "INFO",
                f"signal {event.signal_id} accepted qty={qty}",
            )
        except Exception:
            pass

        # Persist the Signal row before returning (FK needed by OrderExecutor)
        try:
            await self._trading.save_signal(event)
        except Exception:
            logger.warning(
                "RiskFilter: failed to persist signal %s"
                " — order will proceed but audit incomplete",
                event.signal_id,
            )

        fire(self._log_decision("SIGNAL_ACCEPTED", event, SignalAcceptedContext(qty=qty, order_type="MARKET")))
        logger.info(
            "RiskFilter: ACCEPTED signal=%s symbol=%s side=%s qty=%d",
            event.signal_id,
            event.symbol,
            event.side.value,
            qty,
        )
        return ValidatedOrderEvent.from_signal_event(event, qty)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _build_context(self, event: SignalEvent) -> RiskContext:
        now = self._clock.now()
        today = now.date()
        realized_pnl = await self._factory.pnl().get_or_set(
            (today,),
            producer=lambda: self._trading.get_daily_realized_pnl(today),
        )
        position = None
        if event.signal_type == SignalType.ENTRY:
            position = await self._position.get_position(event.symbol, event.instrument_type.value)
        return RiskContext(
            now=now,
            today=today,
            equity=self._config.equity,
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
            await self._audit.log_audit(
                "risk_filter",
                "WARNING",
                f"signal {event.signal_id} rejected: {reason}",
            )
        except Exception:
            logger.debug("RiskFilter: audit log failed for rejection %s", event.signal_id)

    async def _log_decision(self, step: str, event: SignalEvent, context: AuditContext) -> None:
        if event.tick_log_id <= 0:
            return
        try:
            await self._audit.log_decision(
                step=step,
                symbol=event.symbol,
                tick_log_id=event.tick_log_id,
                context=context,
                signal_id=event.signal_id,
            )
        except Exception:
            logger.exception("RiskFilter: decision log failed for signal %s", event.signal_id)
