from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dependency_injector import containers, providers

from trading.config.settings import get_settings
from trading.di.containers.algo_steps import build_algo_steps
from trading.di.containers.broker import BrokerContainer
from trading.di.containers.components import ComponentContainer
from trading.di.containers.infra import InfrastructureContainer
from trading.di.containers.worker_components import WorkerComponentContainer


class AppContainer(containers.DeclarativeContainer):
    """
    Top-level container for the ingestor process.

    Composes InfrastructureContainer -> BrokerContainer -> AlgoSteps ->
    ComponentContainer, wiring each sub-container's declared Dependency
    providers to the providers that satisfy them, mirroring the single
    make_async_container(...) call Dishka used before this migration.
    """

    settings = providers.Singleton(get_settings)

    infra = providers.Container(InfrastructureContainer, settings=settings)

    broker = providers.Container(
        BrokerContainer,
        settings=infra.settings,
        price_store=infra.price_store,
    )

    steps = providers.Singleton(build_algo_steps)

    components = providers.Container(
        ComponentContainer,
        settings=infra.settings,
        clock=infra.clock,
        stream=broker.broker_stream,
        broker=broker.broker,
        client=broker.kite_client,
        sf=infra.session_factory,
        redis=infra.redis_client,
        candle_data_store=infra.candle_data_store,
        trading=infra.trading_store,
        audit=infra.audit_store,
        chart=infra.chart_store,
        config_store=infra.config_store,
        price_store=infra.price_store,
        heartbeat_store=infra.heartbeat_store,
        position_store=infra.position_store,
        cacher_factory=infra.cacher_factory,
        steps=steps,
    )


class WorkerAppContainer(containers.DeclarativeContainer):
    """
    Top-level container for a single-algo worker process.

    Same infra/broker/steps composition as AppContainer, but wires
    WorkerComponentContainer instead of ComponentContainer.
    """

    algo_name = providers.Dependency(instance_of=str)

    settings = providers.Singleton(get_settings)

    infra = providers.Container(InfrastructureContainer, settings=settings)

    broker = providers.Container(
        BrokerContainer,
        settings=infra.settings,
        price_store=infra.price_store,
    )

    steps = providers.Singleton(build_algo_steps)

    worker_components = providers.Container(
        WorkerComponentContainer,
        algo_name=algo_name,
        settings=infra.settings,
        broker=broker.broker,
        sf=infra.session_factory,
        redis=infra.redis_client,
        candle_data_store=infra.candle_data_store,
        trading=infra.trading_store,
        audit=infra.audit_store,
        chart=infra.chart_store,
        config_store=infra.config_store,
        price_store=infra.price_store,
        heartbeat_store=infra.heartbeat_store,
        cacher_factory=infra.cacher_factory,
        steps=steps,
    )


@asynccontextmanager
async def build_container() -> AsyncIterator[AppContainer]:
    """
    Build the DI container for the ingestor process.

    Tests override providers directly, e.g.::

        async with build_container() as c:
            c.infra.settings.override(providers.Object(test_settings))
    """
    container = AppContainer()
    await container.init_resources()
    try:
        yield container
    finally:
        await container.shutdown_resources()


@asynccontextmanager
async def build_worker_container(algo_name: str) -> AsyncIterator[WorkerAppContainer]:
    """
    Build the DI container for a strategy worker process.

    The worker subscribes to Redis pub/sub ticks and runs the full
    candle -> signal -> risk -> execution pipeline for a single named algo::

        async with build_worker_container("ema_crossover") as container:
            runtime = await container.worker_components.runtime()
    """
    container = WorkerAppContainer(algo_name=algo_name)
    await container.init_resources()
    try:
        yield container
    finally:
        await container.shutdown_resources()
