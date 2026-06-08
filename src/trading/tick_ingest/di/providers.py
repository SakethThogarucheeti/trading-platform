from __future__ import annotations

from dishka import Provider, Scope, provide  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.broker.api import BrokerStream
from trading.core.messaging import AbstractCircuitBreaker
from trading.core.models import Instrument
from trading.tick_ingest.service.ingestor import CircuitBreaker, TickConfig, TickIngestor
from trading.tick_ingest.service.publisher import TickPublisher
from trading.tick_ingest.storage.store import TickAuditStore


class TickIngestProvider(Provider):
    """Wires tick_ingest module internals."""

    scope = Scope.APP

    @provide
    def circuit_breaker(self) -> AbstractCircuitBreaker:
        return CircuitBreaker()

    @provide
    def tick_audit_store(
        self, sf: async_sessionmaker[AsyncSession]
    ) -> TickAuditStore:
        return TickAuditStore(sf)

    @provide
    async def tick_ingestor(
        self,
        stream: BrokerStream,
        audit: TickAuditStore,
        circuit: AbstractCircuitBreaker,
        sf: async_sessionmaker[AsyncSession],
    ) -> TickIngestor:
        from sqlalchemy import select

        async with sf() as session:
            instruments = list((await session.execute(select(Instrument))).scalars().all())

        config = TickConfig(instruments=instruments, exec_id="direct")
        return TickIngestor(config=config, stream=stream, audit=audit, circuit=circuit)

    @provide
    def tick_publisher(self, redis: object) -> TickPublisher:
        return TickPublisher(redis)
