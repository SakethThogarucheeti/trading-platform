from __future__ import annotations

from dishka import AsyncContainer, make_async_container
from dishka.provider.base_provider import BaseProvider

from trading.di.providers import BrokerProvider, ComponentProvider, InfrastructureProvider


def build_container(*extra_providers: BaseProvider) -> AsyncContainer:
    """
    Build the DI container.

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
        *extra_providers,
    )
