from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from trading.config.settings import Settings, get_settings
from trading.di.containers.broker import BrokerDeps, build_broker
from trading.di.containers.components import Components, build_components
from trading.di.containers.infra import Infra, build_infra


@dataclass
class IngestorApp:
    """Top-level assembly for the ingestor process: infra, broker, and the
    runtime components built from them."""

    infra: Infra
    broker: BrokerDeps
    components: Components


@asynccontextmanager
async def build_ingestor_app(settings: Settings | None = None) -> AsyncIterator[IngestorApp]:
    """
    Build the app for the ingestor process.

    Plain builder functions replaced the old dependency_injector container
    hierarchy here (trading-platform#3) -- construction is eager, which
    matches what the container actually did at runtime: main.py resolved
    every provider unconditionally at boot, so nothing relied on the
    framework's nominal lazy-singleton semantics.
    """
    settings = settings or get_settings()
    infra = await build_infra(settings)
    try:
        broker = build_broker(infra.settings, infra.price_store, infra.failed_dispatch_store)
        components = await build_components(infra, broker)
        yield IngestorApp(infra=infra, broker=broker, components=components)
    finally:
        await infra.db_engine.dispose()
