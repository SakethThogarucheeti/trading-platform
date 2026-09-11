"""Tests for di/containers/components.py — _RuntimeAssembler.build_runtime.

build_runtime wires the KiteIngestor, the per-algo TickPipelines, and the
CandleAggregatorComponent together (src/trading/di/containers/components.py).
It had zero direct test coverage before this file -- the only related test,
test_algo_pipeline_build_and_wire.py, covers AlgoPipelineFactory.build_and_wire
itself (a level below build_runtime), not build_runtime's own wiring.

AlgoPipelineFactory is patched out in every test here so these tests isolate
build_runtime's own logic (instrument loading, KiteIngestor/CandleAggregator-
Component construction, the per-algo loop, kite_ingestor/order_executor
bookkeeping, final Runtime assembly) from AlgoPipelineFactory's actual
strategy/gate resolution, which is exercised separately in
test_algo_pipeline_build_and_wire.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading.candles.storage.models import Instrument
from trading.config.settings import AlgoSettings, Settings
from trading.core.lifecycle.runtime import Runtime
from trading.di.containers.components import (
    RuntimeDeps,
    _RuntimeAssembler,  # pyright: ignore[reportPrivateUsage]
)


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "zerodha_api_key": "test-key",
        "zerodha_api_secret": "test-secret",
        "token_secret_key": "test-token-secret",
        "postgres_url": "postgresql+asyncpg://u:p@localhost/test",
        "paper_trading": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _mock_sf(instruments: list[Instrument]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = instruments
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    sf = MagicMock()
    sf.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.return_value.__aexit__ = AsyncMock(return_value=False)
    return sf


def _instrument(symbol: str = "INFY", token: int = 101) -> Instrument:
    return Instrument(token=token, symbol=symbol, exchange="NSE", instrument_type="EQUITY")


def _fake_pipeline() -> MagicMock:
    pipeline = MagicMock()
    pipeline.run = MagicMock(name="tick_pipeline.run")
    pipeline.order_executor = MagicMock(name="order_executor")
    return pipeline


def _deps(settings: Settings, instruments: list[Instrument], **overrides: object) -> RuntimeDeps:
    defaults: dict[str, object] = {
        "tick_registry": MagicMock(name="tick_registry"),
        "candle_registry": MagicMock(name="candle_registry"),
        "historical_data_service": MagicMock(name="historical_data_service"),
        "heartbeat_monitor": MagicMock(name="heartbeat_monitor"),
        "stream": MagicMock(name="stream"),
        "broker": MagicMock(name="broker"),
        "trading": MagicMock(name="trading"),
        "audit": MagicMock(name="audit"),
        "chart": MagicMock(name="chart"),
        "config_store": MagicMock(name="config_store"),
        "price_store": MagicMock(name="price_store"),
        "settings": settings,
        "sf": _mock_sf(instruments),
        "circuit": MagicMock(name="circuit"),
        "cacher_factory": MagicMock(name="cacher_factory"),
    }
    defaults.update(overrides)
    return RuntimeDeps(**defaults)  # type: ignore[arg-type]


async def _run_build_runtime(
    settings: Settings,
    instruments: list[Instrument],
    factory_build_and_wire: AsyncMock,
    **dep_overrides: object,
) -> tuple[_RuntimeAssembler, object]:
    assembler = _RuntimeAssembler()
    with patch("trading.di.containers.components.AlgoPipelineFactory") as factory_cls:
        factory_cls.return_value.build_and_wire = factory_build_and_wire
        runtime = await assembler.build_runtime(_deps(settings, instruments, **dep_overrides))
    return assembler, runtime


@pytest.mark.asyncio
async def test_build_runtime_defaults_to_one_algo_when_none_configured() -> None:
    fake_pipeline = _fake_pipeline()
    build_and_wire = AsyncMock(return_value=fake_pipeline)

    _assembler, runtime = await _run_build_runtime(
        _settings(algos=[]), [_instrument()], build_and_wire
    )

    build_and_wire.assert_awaited_once()
    assert build_and_wire.call_args.kwargs["algo"].name == "default"
    assert isinstance(runtime, Runtime)


@pytest.mark.asyncio
async def test_build_runtime_calls_build_and_wire_once_per_configured_algo() -> None:
    fake_pipeline_a, fake_pipeline_b = _fake_pipeline(), _fake_pipeline()
    build_and_wire = AsyncMock(side_effect=[fake_pipeline_a, fake_pipeline_b])
    algos = [
        AlgoSettings(name="algo_a", instruments=["INFY"], equity=10_000.0),
        AlgoSettings(name="algo_b", instruments=["INFY"], equity=10_000.0),
    ]

    _assembler, _runtime = await _run_build_runtime(
        _settings(algos=algos), [_instrument()], build_and_wire
    )

    assert build_and_wire.await_count == 2
    called_names = [c.kwargs["algo"].name for c in build_and_wire.call_args_list]
    assert called_names == ["algo_a", "algo_b"]


@pytest.mark.asyncio
async def test_build_runtime_registers_each_pipeline_run_as_an_on_tick_callback() -> None:
    fake_pipeline_a, fake_pipeline_b = _fake_pipeline(), _fake_pipeline()
    build_and_wire = AsyncMock(side_effect=[fake_pipeline_a, fake_pipeline_b])
    algos = [
        AlgoSettings(name="algo_a", instruments=["INFY"], equity=10_000.0),
        AlgoSettings(name="algo_b", instruments=["INFY"], equity=10_000.0),
    ]

    assembler, _ = await _run_build_runtime(
        _settings(algos=algos), [_instrument()], build_and_wire
    )

    assert assembler.kite_ingestor is not None
    # White-box: KiteIngestor.add_on_tick appends to this list (no public getter).
    callbacks = assembler.kite_ingestor._on_tick_callbacks  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert callbacks == [fake_pipeline_a.run, fake_pipeline_b.run]


@pytest.mark.asyncio
async def test_build_runtime_order_executor_is_the_last_algos_pipeline() -> None:
    fake_pipeline_a, fake_pipeline_b = _fake_pipeline(), _fake_pipeline()
    build_and_wire = AsyncMock(side_effect=[fake_pipeline_a, fake_pipeline_b])
    algos = [
        AlgoSettings(name="algo_a", instruments=["INFY"], equity=10_000.0),
        AlgoSettings(name="algo_b", instruments=["INFY"], equity=10_000.0),
    ]

    assembler, _ = await _run_build_runtime(
        _settings(algos=algos), [_instrument()], build_and_wire
    )

    # Matches the source's own documented last-one-wins behavior (see the
    # comment on _RuntimeAssembler.order_executor in components.py).
    assert assembler.order_executor is fake_pipeline_b.order_executor


@pytest.mark.asyncio
async def test_build_runtime_returns_runtime_wrapping_ingestor_and_candle_aggregator() -> None:
    heartbeat_monitor = MagicMock(name="heartbeat_monitor")
    build_and_wire = AsyncMock(return_value=_fake_pipeline())
    assembler = _RuntimeAssembler()
    deps = _deps(_settings(algos=[]), [_instrument()], heartbeat_monitor=heartbeat_monitor)

    with patch("trading.di.containers.components.AlgoPipelineFactory") as factory_cls:
        factory_cls.return_value.build_and_wire = build_and_wire
        runtime = await assembler.build_runtime(deps)

    assert isinstance(runtime, Runtime)
    components = runtime._components  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert assembler.kite_ingestor in components
    assert heartbeat_monitor in components
    assert len(components) == 3  # ingestor, candle_aggregator, heartbeat_monitor
