from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from trading.broker.service.broker import Broker
from trading.core.schemas import OrderType, Side

logger = logging.getLogger(__name__)


class FaultInjector(Broker):
    """
    Wraps a real Broker and injects faults into place_order() for resilience testing.

    Fault types (configurable):
    - Timeout: raises asyncio.TimeoutError with given probability.
    - Error: raises RuntimeError with given probability.
    - Delay: sleeps for a random amount before forwarding the call.
    - Duplicate: calls the underlying broker twice (returns first order id).

    Multiple fault types compose: timeout is evaluated first, then error,
    then delay, then duplicate.

    Reproducibility
    ---------------
    Pass ``seed`` to fix the RNG for deterministic fault sequences.

    Usage::

        broker = FaultInjector(real_broker, seed=42).with_timeout_rate(0.3)
        exec_reg = ExecRegistry(config=..., broker=broker, ...)
    """

    def __init__(self, broker: Broker, seed: int | None = None) -> None:
        self._broker = broker
        self._rng = random.Random(seed)
        self._timeout_rate: float = 0.0
        self._error_rate: float = 0.0
        self._delay: tuple[float, float] | None = None
        self._dup_rate: float = 0.0

    def with_timeout_rate(self, rate: float) -> FaultInjector:
        """Raise TimeoutError on place_order with probability *rate*."""
        self._timeout_rate = rate
        return self

    def with_error_rate(self, rate: float) -> FaultInjector:
        """Raise RuntimeError on place_order with probability *rate*."""
        self._error_rate = rate
        return self

    def with_delay(self, min_secs: float, max_secs: float) -> FaultInjector:
        """Delay place_order by a random amount in [min_secs, max_secs]."""
        self._delay = (min_secs, max_secs)
        return self

    def with_duplicate_rate(self, rate: float) -> FaultInjector:
        """Call the underlying broker twice with probability *rate*."""
        self._dup_rate = rate
        return self

    # ------------------------------------------------------------------
    # Broker interface
    # ------------------------------------------------------------------

    def get_instruments(self):
        return self._broker.get_instruments()

    def get_ohlc(self, symbol: str, interval: str, start: Any, end: Any):
        return self._broker.get_ohlc(symbol, interval, start, end)

    async def place_order(
        self,
        symbol: str,
        side: Side,
        qty: int,
        order_type: OrderType,
        limit_price: float | None = None,
        instrument_type: str = "EQUITY",
        tick_log_id: int = 0,
    ) -> str:
        # 1. Timeout fault
        if self._timeout_rate > 0 and self._rng.random() < self._timeout_rate:
            logger.debug("FaultInjector: TIMEOUT injected for %s", symbol)
            raise TimeoutError("FaultInjector: simulated broker timeout")

        # 2. Error fault
        if self._error_rate > 0 and self._rng.random() < self._error_rate:
            logger.debug("FaultInjector: ERROR injected for %s", symbol)
            raise RuntimeError("FaultInjector: simulated broker error")

        # 3. Delay
        if self._delay is not None:
            secs = self._rng.uniform(*self._delay)
            await asyncio.sleep(secs)

        # 4. Place
        order_id = await self._broker.place_order(symbol, side, qty, order_type, limit_price)
        logger.debug("FaultInjector: PLACED order %s for %s", order_id, symbol)

        # 5. Duplicate
        if self._dup_rate > 0 and self._rng.random() < self._dup_rate:
            logger.debug("FaultInjector: DUPLICATE call for %s", symbol)
            await self._broker.place_order(symbol, side, qty, order_type, limit_price)

        return order_id
