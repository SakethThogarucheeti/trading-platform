from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

from trading.core.clock import Clock, SystemClock
from trading.core.messaging import AbstractRegistry
from trading.core.schemas import (
    Side,
    SignalEvent,
    SignalType,
    ValidatedOrderEvent,
)
from trading.core.tasks import fire
from trading.tick_ingest.tick_ingestor import CircuitBreaker
from trading.risk.sizer import calculate_quantity
from trading.storage.stores.audit import AbstractAuditStore, AuditContext
from trading.storage.stores.trading import AbstractTradingStore

logger = logging.getLogger(__name__)

_AFTER_CUTOFF = "AFTER_CUTOFF"
_CIRCUIT_OPEN = "CIRCUIT_OPEN"
_DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
_ALREADY_IN_POSITION = "ALREADY_IN_POSITION"
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
    paper_trading: bool = False
    intraday_cutoff_hour: int = Field(default=15, ge=0, le=23)
    intraday_cutoff_minute: int = Field(default=30, ge=0, le=59)


class RiskFilter(AbstractRegistry):
    """
    Evaluates a SignalEvent against risk rules before forwarding it.

    Receives the CircuitBreaker from TickIngestor (same instance) so it can
    check whether the ingestor WebSocket is currently healthy.

    Returns a ValidatedOrderEvent if the signal passes all checks, None if rejected.
    """

    def __init__(
        self,
        config: RiskConfig,
        circuit: CircuitBreaker,
        trading: AbstractTradingStore,
        audit: AbstractAuditStore,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._circuit = circuit
        self._trading = trading
        self._audit = audit
        self._clock: Clock = clock or SystemClock()

    @property
    def config(self) -> RiskConfig:
        return self._config

    # ------------------------------------------------------------------
    # AbstractRegistry
    # ------------------------------------------------------------------

    async def handle(self, event: SignalEvent) -> ValidatedOrderEvent | None:
        """
        Run the 5-step risk pipeline. Returns ValidatedOrderEvent or None.
        """
        from datetime import time

        now = self._clock.now()
        today = now.date()
        cutoff = time(self._config.intraday_cutoff_hour, self._config.intraday_cutoff_minute)

        # 1. Time cutoff
        if now.time() > cutoff:
            await self._reject(event, _AFTER_CUTOFF)
            return None

        # 2. Circuit breaker
        if self._circuit.is_open():
            await self._reject(event, _CIRCUIT_OPEN)
            return None

        # 3. Daily loss limit (skipped in paper mode)
        if not self._config.paper_trading:
            pnl = await self._trading.get_daily_realized_pnl(today)
            limit = self._config.equity * self._config.max_daily_loss_pct / 100.0
            if abs(pnl) > limit:
                await self._reject(event, _DAILY_LOSS_LIMIT)
                return None

        # 4. Duplicate position check
        if event.signal_type == SignalType.ENTRY:
            pos = await self._trading.get_position(event.symbol, event.instrument_type.value)
            if pos is not None and pos.net_qty != 0:
                if (pos.net_qty > 0 and event.side == Side.BUY) or (
                    pos.net_qty < 0 and event.side == Side.SELL
                ):
                    await self._reject(event, _ALREADY_IN_POSITION)
                    return None

        # 5. Size the order
        qty = calculate_quantity(
            stop_distance=event.stop_distance,
            equity=self._config.equity,
            risk_pct=self._config.risk_per_trade_pct,
            lot_size=None,
        )
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
