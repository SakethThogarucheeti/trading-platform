from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from datetime import time

from pydantic import BaseModel, Field
from trading_risk_sdk.policy import RiskContext, RiskGate, RiskSizer
from trading_risk_sdk.sizer import VolatilitySizer

from trading.app.tasks import fire
from trading.core.clock import Clock, SystemClock
from trading.core.messaging import AbstractCircuitBreaker, AbstractRegistry
from trading.core.schemas import SignalType
from trading.monitoring.api.interfaces import AbstractFailedDispatchStore
from trading.risk.api.interfaces import (
    AbstractAuditStore,
    AbstractPositionStore,
    AbstractTradingStore,
)
from trading.risk.api.schemas import ValidatedOrderEvent
from trading.strategy.api.schemas import SignalEvent

logger = logging.getLogger(__name__)

_ZERO_QUANTITY = "ZERO_QUANTITY"
_SIGNAL_PERSIST_FAILED = "SIGNAL_PERSIST_FAILED"


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
        clock: Clock | None = None,
        sizer: RiskSizer | None = None,
        equity_provider: Callable[[], float] | None = None,
        circuit: AbstractCircuitBreaker | None = None,
        failed_dispatch: AbstractFailedDispatchStore | None = None,
    ) -> None:
        self._config = config
        self._gates = gates
        self._trading = trading
        self._audit = audit
        self._position = position
        self._clock: Clock = clock or SystemClock()
        self._sizer: RiskSizer = sizer or VolatilitySizer()
        self._equity_provider = equity_provider
        self._circuit = circuit
        self._failed_dispatch = failed_dispatch

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
            logger.warning("RiskFilter: failed to write audit log for signal %s", event.signal_id)

        try:
            await self._trading.save_signal(event)
        except Exception:
            logger.exception(
                "RiskFilter: failed to persist signal %s -- rejecting rather than "
                "placing an order with a signal_id that was never saved",
                event.signal_id,
            )
            await self._reject(event, _SIGNAL_PERSIST_FAILED)
            return None

        fire(self._log_decision("SIGNAL_ACCEPTED", event, SignalAcceptedContext(qty=qty, order_type="MARKET")))
        logger.info("RiskFilter: ACCEPTED signal=%s symbol=%s side=%s qty=%d", event.signal_id, event.symbol, event.side.value, qty)
        return ValidatedOrderEvent.from_signal_event(event, qty)

    async def _build_context(self, event: SignalEvent) -> RiskContext:
        now = self._clock.now()
        now_local = self._clock.now_tz().time()
        today = self._clock.today()
        realized_pnl = await self._trading.get_pnl_aggregate(today)  # type: ignore[attr-defined]
        position = None
        if event.signal_type == SignalType.ENTRY:
            if event.algo_name is not None:
                # Per-algo exposure (trading-platform#8) -- not the shared `positions`
                # row, which is blended across every algo trading this instrument
                # until that table's PK is widened with algo_name.
                position = await self._position.get_algo_position(
                    event.symbol, event.instrument_type.value, event.algo_name
                )
            else:
                # No algo_name to scope by (e.g. a manually-built SignalEvent) --
                # fall back to the old blended-account view rather than guess.
                position = await self._position.get_position(
                    event.symbol, event.instrument_type.value
                )
        equity = self._equity_provider() if self._equity_provider is not None else self._config.equity
        circuit_open = self._circuit.is_open() if self._circuit is not None else False
        return RiskContext(
            now=now,
            now_local=now_local,
            today=today,
            equity=max(equity, 0.0),
            max_daily_loss_pct=self._config.max_daily_loss_pct,
            risk_per_trade_pct=self._config.risk_per_trade_pct,
            cutoff=time(self._config.intraday_cutoff_hour, self._config.intraday_cutoff_minute),
            realized_pnl=realized_pnl,
            position=position,
            circuit_open=circuit_open,
        )

    async def _reject(self, event: SignalEvent, reason: str) -> None:
        logger.info("RiskFilter: REJECTED signal=%s reason=%s", event.signal_id, reason)
        fire(self._log_decision("SIGNAL_REJECTED", event, SignalRejectedContext(reason=reason)))
        try:
            await self._audit.log_audit("risk_filter", "WARNING", f"signal {event.signal_id} rejected: {reason}")
        except Exception:
            logger.warning("RiskFilter: failed to write audit log for rejected signal %s", event.signal_id)

    async def _log_decision(self, step: str, event: SignalEvent, context: object) -> None:
        if event.tick_log_id <= 0:
            return
        try:
            await self._audit.log_decision(
                step=step, symbol=event.symbol, tick_log_id=event.tick_log_id,
                context=context, algo_name=event.algo_name, signal_id=event.signal_id,
            )
        except Exception as exc:
            logger.exception("RiskFilter: decision log failed for signal %s", event.signal_id)
            if self._failed_dispatch is not None:
                try:
                    await self._failed_dispatch.record(
                        site="decision_log",
                        payload={
                            "step": step,
                            "symbol": event.symbol,
                            "tick_log_id": event.tick_log_id,
                            "algo_name": event.algo_name,
                            "signal_id": str(event.signal_id) if event.signal_id else None,
                            "context": (
                                asdict(context)
                                if is_dataclass(context) and not isinstance(context, type)
                                else None
                            ),
                        },
                        error=str(exc),
                    )
                except Exception:
                    logger.exception(
                        "RiskFilter: failed to persist failed-dispatch record for signal %s",
                        event.signal_id,
                    )
