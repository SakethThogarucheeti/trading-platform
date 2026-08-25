from __future__ import annotations

import logging

import faust

from trading.app.faust_app import ticks_topic
from trading.app.pipeline import TickPipeline
from trading.broker.service.paper_broker import AbstractPriceStore
from trading.core.lifecycle.component import Component
from trading.core.schemas import TickEvent
from trading.worker.circuit_breaker_redis import RedisCircuitBreaker

logger = logging.getLogger(__name__)


class TickAgentComponent(Component):
    """
    Worker-side counterpart to KiteIngestor — faust-streaming replacement for
    TickSubscriber.

    Consumes the shared Kafka ``ticks`` topic (one consumer group per algo,
    derived from the faust App's id) and forwards every tick for this algo's
    instruments to TickPipeline.run — the same candle -> signal -> risk ->
    execution chain TickSubscriber used to drive. Ticks for other instruments
    are skipped, since every algo's worker consumes the full topic but only
    this algo's TickPipeline was built with this algo's SignalGenerator /
    RiskFilter / OrderExecutor instances.

    Also runs circuit_breaker.sync_loop() as a concurrent background task,
    same as TickSubscriber did.

    Lifecycle
    ---------
    _setup:    start the faust App's worker (connects to Kafka, joins the
               consumer group, starts the agent)
    _run:      run circuit sync loop concurrently; block until stop()
    _teardown: stop the faust App
    """

    def __init__(
        self,
        app: faust.App,
        tokens: list[int],
        tick_pipeline: TickPipeline,
        circuit_breaker: RedisCircuitBreaker,
        token_symbol: dict[int, str],
        price_store: AbstractPriceStore | None = None,
    ) -> None:
        super().__init__(name="tick_agent")
        if not tokens:
            # An empty token set means this worker will consume the ticks
            # topic and silently forward nothing, forever — almost always a
            # sign that instrument sync hadn't finished (or found nothing
            # for this algo's configured symbols) before this worker was
            # built. Fail fast instead of running dark.
            raise ValueError(
                "TickAgentComponent built with an empty token set — this worker "
                "would consume ticks and match nothing. Check instrument sync "
                "completed and this algo's configured symbols resolved to tokens."
            )
        self._app = app
        self._tokens = set(tokens)
        self._tick_pipeline = tick_pipeline
        self._circuit_breaker = circuit_breaker
        self._token_symbol = token_symbol
        self._price_store = price_store
        self._topic = ticks_topic(app)
        self._agent = app.agent(self._topic)(self._process_ticks)
        self._worker: faust.Worker | None = None

    async def _process_ticks(self, stream: faust.Stream) -> None:
        async for raw in stream:
            try:
                tick = TickEvent.model_validate_json(raw)
            except Exception:
                logger.debug("TickAgentComponent: failed to deserialize tick")
                continue

            if tick.instrument_token not in self._tokens:
                continue

            if self._price_store is not None:
                symbol = self._token_symbol.get(tick.instrument_token, "")
                if symbol:
                    self._price_store.update(symbol, tick.last_price)

            try:
                await self._tick_pipeline.run(tick)
            except Exception:
                logger.exception("TickAgentComponent: tick_pipeline.run error")

    async def _setup(self) -> None:
        self._app.finalize()
        # daemon=False: faust.Worker.on_started() awaits wait_until_stopped()
        # when daemon (the default), so maybe_start() only returns once the
        # worker is told to stop. TickAgentComponent embeds the worker inside
        # our own Component/Runtime lifecycle, so it must start and hand
        # control back — Runtime.stop() drives shutdown via _teardown().
        self._worker = faust.Worker(self._app, loglevel="WARN", daemon=False)
        await self._worker.maybe_start()
        logger.info("TickAgentComponent: faust worker started, consuming %d tokens", len(self._tokens))

    async def _run(self) -> None:
        from anyio import create_task_group, sleep_forever

        async with create_task_group() as tg:
            tg.start_soon(self._circuit_breaker.sync_loop)
            await sleep_forever()

    async def _teardown(self) -> None:
        if self._worker is not None:
            await self._worker.stop()
            self._worker = None
