from __future__ import annotations

from dishka import AsyncContainer, make_async_container
from dishka.provider.base_provider import BaseProvider

from trading.di.providers import BrokerProvider, ComponentProvider, InfrastructureProvider, RedisProvider, WorkerComponentProvider


def build_container(*extra_providers: BaseProvider) -> AsyncContainer:
    """
    Build the DI container for the ingestor process.

    Production::

        async with build_container() as container:
            bus = await container.get(MessageBus)

    Tests (swap infra + broker without touching prod code)::

        async with build_container(TestInfraProvider(), TestBrokerProvider()) as c:
            yield c

    Extra providers are appended last, so their bindings override the
    defaults when dishka resolves by type.
    """
    return make_async_container(
        InfrastructureProvider(),
        BrokerProvider(),
        ComponentProvider(),
        RedisProvider(),
        *extra_providers,
    )


def build_worker_container(algo_name: str, *extra_providers: BaseProvider) -> AsyncContainer:
    """
    Build the DI container for a strategy worker process.

    The worker subscribes to Redis pub/sub ticks and runs the full
    candle → signal → risk → execution pipeline for a single named algo.

    Usage::

        async with build_worker_container("ema_crossover") as container:
            runtime = await container.get(AbstractRuntime)
    """
    return make_async_container(
        InfrastructureProvider(),
        BrokerProvider(),
        WorkerComponentProvider(algo_name),
        RedisProvider(),
        *extra_providers,
    )
