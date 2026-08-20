"""Tests for di/providers/algo_pipeline.py — AlgoPipelineFactory.seed_state.

Regression coverage for a bug where every process restart called
upsert_algo_state() unconditionally with a brand-new AlgoInstance's blank
state (bars_seen=0, warmup_complete=False), wiping out a running algo's real,
already-persisted warmup progress. seed_state() is not gated behind market
hours — it runs on every container boot — so a running algo that got
restarted (e.g. to deploy an unrelated fix) would show as cold/unwarmed on
the dashboard and in the daily report until its next real candle close,
which could be hours later at the next market open.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from trading.config.settings import AlgoSettings
from trading.di.providers.algo_pipeline import AlgoPipelineFactory, SharedAlgoDeps


def _make_factory(config_store: MagicMock) -> AlgoPipelineFactory:
    strategy = MagicMock()
    strategy.get_params.return_value = {}
    strategy.get_state.return_value = {}

    steps = MagicMock()
    steps.strategy.return_value = strategy

    shared = SharedAlgoDeps(
        chart=MagicMock(),
        config_store=config_store,
        audit=MagicMock(),
        trading=MagicMock(),
        broker=MagicMock(),
        session_factory=MagicMock(),
        polars_store=MagicMock(),
        settings=MagicMock(warmup_candles=200),
        factory=MagicMock(),
        steps=steps,
    )
    return AlgoPipelineFactory(shared)


def _make_algo(name: str = "default") -> AlgoSettings:
    return AlgoSettings(name=name, instruments=["INFY"], equity=20000.0)


async def test_seed_state_writes_blank_state_for_brand_new_algo() -> None:
    config_store = MagicMock()
    config_store.seed_algo_config = AsyncMock()
    config_store.get_algo_state = AsyncMock(return_value=None)
    config_store.upsert_algo_state = AsyncMock()

    factory = _make_factory(config_store)
    await factory.seed_state(_make_algo(), intervals=["1min"])

    config_store.upsert_algo_state.assert_called_once()
    name, state = config_store.upsert_algo_state.call_args[0]
    assert name == "default"
    assert state["bars_seen"] == 0
    assert state["warmup_complete"] is False


async def test_seed_state_does_not_clobber_existing_progress() -> None:
    """The core regression: an algo that already has real, persisted progress
    must not have it overwritten with a fresh bars_seen=0 state on restart."""
    config_store = MagicMock()
    config_store.seed_algo_config = AsyncMock()
    config_store.get_algo_state = AsyncMock(
        return_value={"bars_seen": 398, "warmup_complete": True}
    )
    config_store.upsert_algo_state = AsyncMock()

    factory = _make_factory(config_store)
    await factory.seed_state(_make_algo(), intervals=["1min"])

    config_store.upsert_algo_state.assert_not_called()


async def test_seed_state_always_refreshes_config() -> None:
    """Config (equity/params/strategy_id) should still refresh on every
    restart — only the runtime *state* must be preserved."""
    config_store = MagicMock()
    config_store.seed_algo_config = AsyncMock()
    config_store.get_algo_state = AsyncMock(
        return_value={"bars_seen": 398, "warmup_complete": True}
    )
    config_store.upsert_algo_state = AsyncMock()

    factory = _make_factory(config_store)
    algo = _make_algo()
    await factory.seed_state(algo, intervals=["1min"])

    config_store.seed_algo_config.assert_called_once()
    kwargs = config_store.seed_algo_config.call_args.kwargs
    assert kwargs["name"] == "default"
    assert kwargs["equity"] == 20000.0
