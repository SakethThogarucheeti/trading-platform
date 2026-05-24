from __future__ import annotations

import logging

from pydantic import BaseModel

from trading.broker.base.broker_stream import BrokerStream
from trading.broker.types import Tick
from trading.core.clock import Clock, SystemClock
from trading.core.messaging import AbstractCircuitBreaker, AbstractRegistry
from trading.core.models import Instrument
from trading.core.schemas import InstrumentType, TickEvent
from trading.storage.stores.audit import AbstractAuditStore

logger = logging.getLogger(__name__)


class TickConfig(BaseModel):
    """Configuration for the tick ingestion stage."""

    model_config = {"arbitrary_types_allowed": True}

    instruments: list[Instrument]
    exec_id: str = "direct"  # "paper" | "direct"


class CircuitBreaker(AbstractCircuitBreaker):
    """
    In-process circuit breaker.

    Opened by KiteIngestor after 30s of disconnection.
    Closed on reconnect. Read by RiskFilter before placing orders.
    """

    def __init__(self) -> None:
        self._open = False

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def is_open(self) -> bool:
        return self._open


class TickIngestor(AbstractRegistry):
    """
    Ingests raw WebSocket ticks, persists them to DB, and produces TickEvents.

    Call handle(raw_tick_dict) for each tick arriving from the broker stream.
    Returns a TickEvent on success, None if the tick is invalid or unknown.
    """

    def __init__(
        self,
        config: TickConfig,
        stream: BrokerStream,
        audit: AbstractAuditStore,
        circuit: AbstractCircuitBreaker,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._stream = stream
        self._audit = audit
        self.circuit = circuit
        self._clock: Clock = clock or SystemClock()

        self._token_type: dict[int, InstrumentType] = {
            inst.token: InstrumentType(inst.instrument_type) for inst in config.instruments
        }
        self._token_symbol: dict[int, str] = {
            inst.token: inst.symbol for inst in config.instruments
        }

    # ------------------------------------------------------------------
    # AbstractRegistry
    # ------------------------------------------------------------------

    async def handle(self, raw: Tick) -> TickEvent | None:  # type: ignore[override]
        """
        Validate and persist one raw tick dict from the broker WebSocket.

        Returns a TickEvent with a real DB-assigned tick_log_id, or None
        if the tick is for an unknown instrument or fails validation.
        """
        token: int | None = raw.get("instrument_token")
        if token is None:
            return None

        instrument_type = self._token_type.get(token)
        if instrument_type is None:
            return None

        symbol = self._token_symbol[token]

        last_price: float = raw.get("last_price", 0.0)
        if not last_price:
            return None

        raw_event = TickEvent(
            instrument_token=token,
            instrument_type=instrument_type,
            last_price=last_price,
            volume=raw.get("volume_traded", raw.get("volume", 0)),
            timestamp=self._clock.now(),
            tick_log_id=0,
        )

        try:
            tick_log_id = await self._audit.log_tick(raw_event, symbol)
        except Exception as exc:
            logger.warning("TickIngestor: DB persist failed for token %s — %s", token, exc)
            tick_log_id = -1

        return raw_event.model_copy(update={"tick_log_id": tick_log_id})

    # ------------------------------------------------------------------
    # Public accessors (avoid private attribute access from KiteIngestor)
    # ------------------------------------------------------------------

    def get_tokens(self) -> list[int]:
        """Return all instrument tokens registered for subscription."""
        return list(self._token_type.keys())

    def get_symbol(self, token: int) -> str | None:
        """Return the trading symbol for a given instrument token, or None."""
        return self._token_symbol.get(token)
