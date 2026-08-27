"""Tests for di/providers/algo_pipeline.py — AlgoPipelineFactory.build_and_wire.

build_and_wire() is the build_pipeline -> seed_state -> add_algo_registry
sequence extracted out of ComponentContainer.build_runtime's per-algo loop
(previously duplicated inline, see trading-platform#13). build_runtime has
no test coverage of its own (it requires a fully wired DB/broker runtime to
exercise), so this covers the actual risk surface of the extraction
directly: that the three calls still happen, in order, with the right
arguments passed through.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from trading.config.settings import AlgoSettings
from trading.di.providers.algo_pipeline import AlgoPipelineFactory, SharedAlgoDeps


def _make_factory(config_store: MagicMock | None = None) -> AlgoPipelineFactory:
    if config_store is None:
        config_store = MagicMock()
        config_store.seed_algo_config = AsyncMock()
        config_store.get_algo_state = AsyncMock(return_value={"bars_seen": 0})
        config_store.upsert_algo_state = AsyncMock()

    shared = SharedAlgoDeps(
        chart=MagicMock(),
        config_store=config_store,
        audit=MagicMock(),
        trading=MagicMock(),
        broker=MagicMock(),
        session_factory=MagicMock(),
        polars_store=MagicMock(),
        settings=MagicMock(warmup_candles=200, paper_trading=True),
        factory=MagicMock(),
    )
    return AlgoPipelineFactory(shared)


def _make_algo(name: str = "default") -> AlgoSettings:
    return AlgoSettings(name=name, instruments=["INFY"], equity=20000.0)


async def test_build_and_wire_seeds_state() -> None:
    config_store = MagicMock()
    config_store.seed_algo_config = AsyncMock()
    config_store.get_algo_state = AsyncMock(return_value={"bars_seen": 0})
    config_store.upsert_algo_state = AsyncMock()

    factory = _make_factory(config_store)
    algo = _make_algo()
    circuit = MagicMock()
    candle_registry = MagicMock()
    registry_target = MagicMock()

    await factory.build_and_wire(
        algo=algo,
        intervals=["1min"],
        instrument_type_map={},
        circuit=circuit,
        candle_registry=candle_registry,
        registry_target=registry_target,
    )

    config_store.seed_algo_config.assert_called_once()
    assert config_store.seed_algo_config.call_args.kwargs["name"] == "default"


async def test_build_and_wire_wires_signal_generator_into_registry_target() -> None:
    factory = _make_factory()
    algo = _make_algo()
    registry_target = MagicMock()

    tick_pipeline = await factory.build_and_wire(
        algo=algo,
        intervals=["1min"],
        instrument_type_map={},
        circuit=MagicMock(),
        candle_registry=MagicMock(),
        registry_target=registry_target,
    )

    registry_target.add_algo_registry.assert_called_once_with(tick_pipeline.signal_generator)


async def test_build_and_wire_passes_circuit_and_candle_registry_through() -> None:
    factory = _make_factory()
    algo = _make_algo()
    circuit = MagicMock(name="circuit")
    candle_registry = MagicMock(name="candle_registry")
    intervals = ["1min"]
    instrument_type_map = {"INFY": "EQUITY"}

    fake_tick_pipeline = MagicMock()
    factory.build_pipeline = MagicMock(return_value=fake_tick_pipeline)  # type: ignore[method-assign]

    result = await factory.build_and_wire(
        algo=algo,
        intervals=intervals,
        instrument_type_map=instrument_type_map,
        circuit=circuit,
        candle_registry=candle_registry,
        registry_target=MagicMock(),
    )

    factory.build_pipeline.assert_called_once_with(
        algo=algo,
        intervals=intervals,
        instrument_type_map=instrument_type_map,
        circuit=circuit,
        candle_registry=candle_registry,
    )
    assert result is fake_tick_pipeline


async def test_build_and_wire_returns_pipeline_for_correct_algo() -> None:
    factory = _make_factory()
    algo = _make_algo(name="my_algo")

    tick_pipeline = await factory.build_and_wire(
        algo=algo,
        intervals=["1min"],
        instrument_type_map={},
        circuit=MagicMock(),
        candle_registry=MagicMock(),
        registry_target=MagicMock(),
    )

    assert tick_pipeline.signal_generator.config.algo_name == "my_algo"
