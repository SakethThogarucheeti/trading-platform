"""
AlgoPipelineFactory.seed_state() — restart must not clobber real progress.

Regression test for a bug found in production: seed_state() runs on every
process boot (it's not gated behind market hours) and unconditionally
overwrote AlgoState with a brand-new AlgoInstance's blank state
(bars_seen=0, warmup_complete=False) — discovered live when redeploying a
container wiped out a running algo's real 398-candle warmup progress, and
the dashboard/daily report kept showing it as cold until the next actual
candle close.

Uses the real ConfigStore + real strategy factory (trading_strategy_sdk's
create_strategy(), called directly by AlgoPipelineFactory) against the test
Postgres container — no mocks on the path this bug actually lived on.
"""

from __future__ import annotations

from trading.config.settings import AlgoSettings, Settings
from trading.di.providers.algo_pipeline import AlgoPipelineFactory, SharedAlgoDeps
from trading.strategy.storage.store import ConfigStore


def _make_settings() -> Settings:
    return Settings(
        zerodha_api_key="k",
        zerodha_api_secret="s",
        token_secret_key="t",
        postgres_url="postgresql+asyncpg://u:p@localhost/t",
    )


def _make_factory(session_factory, settings: Settings) -> AlgoPipelineFactory:
    shared = SharedAlgoDeps(
        chart=None,
        config_store=ConfigStore(session_factory),
        audit=None,
        trading=None,
        broker=None,
        session_factory=session_factory,
        polars_store=None,
        settings=settings,
        factory=None,
    )
    return AlgoPipelineFactory(shared)


def _algo(name: str = "default") -> AlgoSettings:
    return AlgoSettings(name=name, instruments=["INFY"], strategy_id="ema_crossover", equity=20000.0)


async def test_seed_state_preserves_real_warmup_progress(engine, session_factory):
    settings = _make_settings()
    config_store = ConfigStore(session_factory)
    factory = _make_factory(session_factory, settings)
    algo = _algo()

    # First boot: no prior state — seed_state() must create one.
    await factory.seed_state(algo, intervals=["1min"])
    state = await config_store.get_algo_state("default")
    assert state is not None
    assert state["bars_seen"] == 0

    # Simulate real trading progress accumulating (what actually happens as
    # live candles close), the same way SignalGenerator._upsert_state does.
    await config_store.upsert_algo_state(
        "default",
        {"bars_seen": 398, "warmup_candles": 200, "warmup_complete": True, "ema_12": 1129.29},
    )

    # Simulate a container restart: seed_state() runs again unconditionally.
    await factory.seed_state(algo, intervals=["1min"])

    state_after_restart = await config_store.get_algo_state("default")
    assert state_after_restart is not None
    assert state_after_restart["bars_seen"] == 398
    assert state_after_restart["warmup_complete"] is True
    assert state_after_restart["ema_12"] == 1129.29


async def test_seed_state_still_refreshes_config_on_restart(engine, session_factory):
    """Only the runtime *state* must survive restarts unchanged — config
    (equity, params, strategy_id) should still pick up new values."""
    settings = _make_settings()
    config_store = ConfigStore(session_factory)
    factory = _make_factory(session_factory, settings)

    await factory.seed_state(_algo(), intervals=["1min"])
    await config_store.upsert_algo_state("default", {"bars_seen": 398})

    updated_algo = AlgoSettings(
        name="default", instruments=["INFY"], strategy_id="ema_crossover", equity=50000.0
    )
    await factory.seed_state(updated_algo, intervals=["1min"])

    async with session_factory() as session:
        from trading.strategy.storage.models import AlgoConfig

        cfg = await session.get(AlgoConfig, "default")
    assert cfg is not None
    assert cfg.equity == 50000.0

    state = await config_store.get_algo_state("default")
    assert state is not None
    assert state["bars_seen"] == 398


async def test_seed_state_brand_new_algo_gets_blank_state(engine, session_factory):
    settings = _make_settings()
    config_store = ConfigStore(session_factory)
    factory = _make_factory(session_factory, settings)

    await factory.seed_state(_algo("rsi_mean_reversion"), intervals=["1min"])

    state = await config_store.get_algo_state("rsi_mean_reversion")
    assert state is not None
    assert state["bars_seen"] == 0
    assert state["warmup_complete"] is False
