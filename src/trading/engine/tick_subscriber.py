from __future__ import annotations

import logging

from anyio import create_task_group

from trading.broker.paper_broker import AbstractPriceStore
from trading.core.schemas import TickEvent
from trading.core.types import OnTickCallback
from trading.engine.circuit_breaker_redis import RedisCircuitBreaker
from trading.engine.component import Component

logger = logging.getLogger(__name__)


class TickSubscriber(Component):
    """
    Worker-side counterpart to KiteIngestor.

    Subscribes to ``ticks:<token>`` Redis pub/sub channels and forwards each
    deserialized TickEvent to registered on-tick callbacks — the same interface
    as ``KiteIngestor.add_on_tick``.

    Also runs ``circuit_breaker.sync_loop()`` as a concurrent background task
    so the worker's RiskFilter always has a fresh circuit state.

    Lifecycle
    ---------
    _setup:    subscribe to Redis channels
    _run:      listen for messages + run circuit sync loop concurrently
    _teardown: unsubscribe and close the pubsub handle
    """

    def __init__(
        self,
        redis: object,
        tokens: list[int],
        circuit_breaker: RedisCircuitBreaker,
        token_symbol: dict[int, str],
        price_store: AbstractPriceStore | None = None,
    ) -> None:
        super().__init__(name="tick_subscriber")
        self._redis = redis
        self._tokens = tokens
        self._circuit_breaker = circuit_breaker
        self._token_symbol = token_symbol
        self._price_store = price_store
        self._on_tick_callbacks: list[OnTickCallback] = []
        self._pubsub: object | None = None

    def add_on_tick(self, callback: OnTickCallback) -> None:
        self._on_tick_callbacks.append(callback)

    async def _setup(self) -> None:
        pubsub = self._redis.pubsub()  # type: ignore[attr-defined]
        channels = [f"ticks:{t}" for t in self._tokens]
        await pubsub.subscribe(*channels)
        self._pubsub = pubsub
        logger.info("TickSubscriber: subscribed to %d channels", len(channels))

    async def _run(self) -> None:
        async with create_task_group() as tg:
            tg.start_soon(self._circuit_breaker.sync_loop)
            tg.start_soon(self._listen)

    async def _listen(self) -> None:
        pubsub = self._pubsub
        assert pubsub is not None
        async for message in pubsub.listen():  # type: ignore[attr-defined]
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if not data:
                continue
            try:
                tick = TickEvent.model_validate_json(data)
            except Exception:
                logger.debug("TickSubscriber: failed to deserialize tick")
                continue

            if self._price_store is not None:
                symbol = self._token_symbol.get(tick.instrument_token, "")
                if symbol:
                    self._price_store.update(symbol, tick.last_price)  # type: ignore[attr-defined]

            for callback in self._on_tick_callbacks:
                try:
                    await callback(tick)
                except Exception:
                    logger.exception("TickSubscriber: on_tick callback error")

    async def _teardown(self) -> None:
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe()  # type: ignore[attr-defined]
                await self._pubsub.aclose()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._pubsub = None
