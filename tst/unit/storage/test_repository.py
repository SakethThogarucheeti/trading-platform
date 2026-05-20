"""Tests for storage/stores — domain store classes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.core.database import build_session_factory, get_session, init_db
from trading.core.models import Instrument, Order, Signal
from trading.core.schemas import (
    FillEvent,
    InstrumentType,
    OrderStatus,
    Side,
    SignalEvent,
    SignalType,
)
from trading.storage.stores.audit import AuditStore
from trading.storage.stores.chart import ChartStore
from trading.storage.stores.config import ConfigStore
from trading.storage.stores.heartbeat import HeartbeatStore
from trading.storage.stores.instrument import InstrumentStore
from trading.storage.stores.trading import NotFoundError, TradingStore

NOW = datetime.now(UTC)
TODAY = NOW.date()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
def instrument_store(engine: AsyncEngine) -> InstrumentStore:
    return InstrumentStore(build_session_factory(engine))


@pytest.fixture
def trading_store(engine: AsyncEngine) -> TradingStore:
    return TradingStore(build_session_factory(engine))


@pytest.fixture
def audit_store(engine: AsyncEngine) -> AuditStore:
    return AuditStore(build_session_factory(engine))


@pytest.fixture
def heartbeat_store(engine: AsyncEngine) -> HeartbeatStore:
    return HeartbeatStore(build_session_factory(engine))


@pytest.fixture
def config_store(engine: AsyncEngine) -> ConfigStore:
    return ConfigStore(build_session_factory(engine))


def make_signal_event(**overrides: object) -> SignalEvent:
    base = dict(
        signal_id=uuid4(),
        strategy_id="ema_cross",
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        signal_type=SignalType.ENTRY,
        stop_distance=10.0,
        timestamp=NOW,
        tick_log_id=1,
    )
    return SignalEvent(**{**base, **overrides})  # type: ignore[arg-type]


def make_fill(**overrides: object) -> FillEvent:
    base = dict(
        kite_order_id="K001",
        avg_price=100.0,
        filled_qty=10,
        timestamp=NOW,
    )
    return FillEvent(**{**base, **overrides})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------------


async def test_get_instrument_returns_none_for_missing(instrument_store: InstrumentStore) -> None:
    result = await instrument_store.get_instrument(9999)
    assert result is None


async def test_upsert_and_get_instrument(instrument_store: InstrumentStore) -> None:
    inst = Instrument(token=1, symbol="INFY", exchange="NSE", instrument_type="EQUITY")
    await instrument_store.upsert_instruments([inst])
    fetched = await instrument_store.get_instrument(1)
    assert fetched is not None
    assert fetched.symbol == "INFY"


async def test_upsert_updates_existing_instrument(instrument_store: InstrumentStore) -> None:
    inst = Instrument(token=2, symbol="TCS", exchange="NSE", instrument_type="EQUITY")
    await instrument_store.upsert_instruments([inst])
    updated = Instrument(token=2, symbol="TCS", exchange="BSE", instrument_type="EQUITY")
    await instrument_store.upsert_instruments([updated])
    fetched = await instrument_store.get_instrument(2)
    assert fetched is not None
    assert fetched.exchange == "BSE"


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


async def test_save_signal_persists(engine: AsyncEngine, trading_store: TradingStore) -> None:
    event = make_signal_event()
    await trading_store.save_signal(event)
    async with get_session(engine) as s:
        sig = await s.get(Signal, event.signal_id)
    assert sig is not None
    assert sig.strategy_id == "ema_cross"
    assert sig.symbol == "INFY"


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


async def _insert_signal(trading_store: TradingStore) -> object:
    event = make_signal_event()
    await trading_store.save_signal(event)
    return event.signal_id


async def test_save_and_get_order(engine: AsyncEngine, trading_store: TradingStore) -> None:
    sig_id = await _insert_signal(trading_store)
    order = Order(
        id=uuid4(),
        kite_order_id="K100",
        signal_id=sig_id,
        status=OrderStatus.PLACED.value,
        qty=5,
        avg_price=Decimal("0"),
        created_at=NOW,
    )
    await trading_store.save_order(order)
    fetched = await trading_store.get_order_by_kite_id("K100")
    assert fetched is not None
    assert fetched.qty == 5


async def test_get_order_by_kite_id_missing_returns_none(trading_store: TradingStore) -> None:
    result = await trading_store.get_order_by_kite_id("NONEXISTENT")
    assert result is None


async def test_update_order_status(trading_store: TradingStore) -> None:
    sig_id = await _insert_signal(trading_store)
    await trading_store.save_order(
        Order(
            id=uuid4(),
            kite_order_id="K200",
            signal_id=sig_id,
            status=OrderStatus.PLACED.value,
            qty=10,
            avg_price=Decimal("0"),
            created_at=NOW,
        )
    )
    await trading_store.update_order_status("K200", OrderStatus.FILLED, avg_price=150.0)
    order = await trading_store.get_order_by_kite_id("K200")
    assert order is not None
    assert order.status == OrderStatus.FILLED.value
    assert float(order.avg_price) == 150.0


async def test_update_order_status_missing_raises(trading_store: TradingStore) -> None:
    with pytest.raises(NotFoundError):
        await trading_store.update_order_status("GHOST", OrderStatus.FILLED)


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


async def test_get_position_missing_returns_none(trading_store: TradingStore) -> None:
    result = await trading_store.get_position("INFY", "EQUITY")
    assert result is None


async def test_update_position_creates_on_first_buy(trading_store: TradingStore) -> None:
    fill = make_fill(avg_price=100.0, filled_qty=10)
    await trading_store.update_position(fill, Side.BUY, "INFY", "EQUITY")
    pos = await trading_store.get_position("INFY", "EQUITY")
    assert pos is not None
    assert pos.net_qty == 10
    assert float(pos.avg_price) == 100.0


async def test_update_position_adds_to_existing_long(trading_store: TradingStore) -> None:
    fill1 = make_fill(avg_price=100.0, filled_qty=10)
    fill2 = make_fill(avg_price=110.0, filled_qty=10)
    await trading_store.update_position(fill1, Side.BUY, "TCS", "EQUITY")
    await trading_store.update_position(fill2, Side.BUY, "TCS", "EQUITY")
    pos = await trading_store.get_position("TCS", "EQUITY")
    assert pos is not None
    assert pos.net_qty == 20
    assert float(pos.avg_price) == pytest.approx(105.0)


async def test_update_position_sell_reduces_qty(trading_store: TradingStore) -> None:
    fill_buy = make_fill(avg_price=100.0, filled_qty=10)
    fill_sell = make_fill(avg_price=120.0, filled_qty=10)
    await trading_store.update_position(fill_buy, Side.BUY, "RELIANCE", "EQUITY")
    await trading_store.update_position(fill_sell, Side.SELL, "RELIANCE", "EQUITY")
    pos = await trading_store.get_position("RELIANCE", "EQUITY")
    assert pos is not None
    assert pos.net_qty == 0


async def test_update_position_sell_goes_short(trading_store: TradingStore) -> None:
    """Selling more than owned (futures short) produces negative net_qty."""
    fill_buy = make_fill(avg_price=100.0, filled_qty=10)
    fill_sell = make_fill(avg_price=90.0, filled_qty=15)
    await trading_store.update_position(fill_buy, Side.BUY, "NIFTY", "FUTURES")
    await trading_store.update_position(fill_sell, Side.SELL, "NIFTY", "FUTURES")
    pos = await trading_store.get_position("NIFTY", "FUTURES")
    assert pos is not None
    assert pos.net_qty == -5
    assert float(pos.avg_price) == 90.0  # new avg is the fill price when short


async def test_position_composite_pk_independent(trading_store: TradingStore) -> None:
    """INFY EQUITY and INFY FUTURES are tracked independently."""
    fill = make_fill(avg_price=1500.0, filled_qty=5)
    await trading_store.update_position(fill, Side.BUY, "INFY", "EQUITY")
    fill2 = make_fill(avg_price=1510.0, filled_qty=75)
    await trading_store.update_position(fill2, Side.BUY, "INFY", "FUTURES")
    eq = await trading_store.get_position("INFY", "EQUITY")
    fut = await trading_store.get_position("INFY", "FUTURES")
    assert eq is not None and eq.net_qty == 5
    assert fut is not None and fut.net_qty == 75


# ---------------------------------------------------------------------------
# Daily P&L
# ---------------------------------------------------------------------------


async def test_get_daily_realized_pnl_returns_zero_for_no_fills(trading_store: TradingStore) -> None:
    pnl = await trading_store.get_daily_realized_pnl(TODAY)
    assert pnl == 0.0


async def test_get_daily_realized_pnl_sums_filled_orders(
    engine: AsyncEngine, trading_store: TradingStore
) -> None:
    sig_id = await _insert_signal(trading_store)
    async with get_session(engine) as s:
        s.add(
            Order(
                id=uuid4(),
                kite_order_id="K301",
                signal_id=sig_id,
                status=OrderStatus.FILLED.value,
                qty=10,
                avg_price=Decimal("100"),
                created_at=NOW,
            )
        )
        s.add(
            Order(
                id=uuid4(),
                kite_order_id="K302",
                signal_id=sig_id,
                status=OrderStatus.FILLED.value,
                qty=5,
                avg_price=Decimal("200"),
                created_at=NOW,
            )
        )
        # PLACED order — should NOT count
        s.add(
            Order(
                id=uuid4(),
                kite_order_id="K303",
                signal_id=sig_id,
                status=OrderStatus.PLACED.value,
                qty=20,
                avg_price=Decimal("150"),
                created_at=NOW,
            )
        )

    pnl = await trading_store.get_daily_realized_pnl(TODAY)
    # BUY orders are cash outflow: -(10*100 + 5*200) = -2000
    assert pnl == pytest.approx(-2000.0)


# ---------------------------------------------------------------------------
# Heartbeats
# ---------------------------------------------------------------------------


async def test_update_heartbeat_creates_entry(engine: AsyncEngine, heartbeat_store: HeartbeatStore) -> None:
    await heartbeat_store.update_heartbeat("ingestor")
    async with get_session(engine) as s:
        from trading.core.models import Heartbeat
        hb = await s.get(Heartbeat, "ingestor")
    assert hb is not None


async def test_update_heartbeat_upserts(engine: AsyncEngine, heartbeat_store: HeartbeatStore) -> None:
    await heartbeat_store.update_heartbeat("candle_aggregator")
    await heartbeat_store.update_heartbeat("candle_aggregator")  # second call, same module
    async with get_session(engine) as s:
        from sqlalchemy import func, select
        from trading.core.models import Heartbeat
        count = await s.execute(select(func.count()).where(Heartbeat.module == "candle_aggregator"))
    assert count.scalar() == 1  # only one row, not two


async def test_get_stale_modules_empty_when_fresh(heartbeat_store: HeartbeatStore) -> None:
    await heartbeat_store.update_heartbeat("executor")
    stale = await heartbeat_store.get_stale_modules(timeout_secs=60)
    assert "executor" not in stale


async def test_get_stale_modules_detects_old_heartbeat(
    engine: AsyncEngine, heartbeat_store: HeartbeatStore
) -> None:
    from trading.core.models import Heartbeat
    old_ts = datetime.now(UTC) - timedelta(seconds=120)
    async with get_session(engine) as s:
        s.add(Heartbeat(module="zombie", last_seen=old_ts))
    stale = await heartbeat_store.get_stale_modules(timeout_secs=60)
    assert "zombie" in stale


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


async def test_log_audit_appends(engine: AsyncEngine, audit_store: AuditStore) -> None:
    await audit_store.log_audit("risk", "WARNING", "daily loss limit hit")
    await audit_store.log_audit("risk", "INFO", "signal rejected")
    async with get_session(engine) as s:
        from sqlalchemy import select
        from trading.core.models import AuditLog
        result = await s.execute(select(AuditLog))
        logs = result.scalars().all()
    assert len(logs) == 2
    messages = {log.message for log in logs}
    assert "daily loss limit hit" in messages
    assert "signal rejected" in messages


async def test_log_audit_never_raises_on_repeated_calls(audit_store: AuditStore) -> None:
    for i in range(5):
        await audit_store.log_audit("monitor", "INFO", f"heartbeat {i}")
    # no exception raised


# ---------------------------------------------------------------------------
# AlgoConfig / AlgoState
# ---------------------------------------------------------------------------


async def test_seed_algo_config_creates_new(engine: AsyncEngine, config_store: ConfigStore) -> None:
    from trading.core.models import AlgoConfig as AlgoConfigModel

    await config_store.seed_algo_config(
        name="test_algo",
        strategy_id="ema_crossover",
        warmup_candles=200,
        candle_intervals=["1min"],
        equity=10_000.0,
        params={"fast": 9},
    )
    async with get_session(engine) as s:
        cfg = await s.get(AlgoConfigModel, "test_algo")
    assert cfg is not None
    assert cfg.strategy_id == "ema_crossover"


async def test_seed_algo_config_skips_existing(engine: AsyncEngine, config_store: ConfigStore) -> None:
    """Calling seed_algo_config twice should not overwrite or error."""
    from trading.core.models import AlgoConfig as AlgoConfigModel

    await config_store.seed_algo_config(
        name="dup_algo",
        strategy_id="ema_crossover",
        warmup_candles=200,
        candle_intervals=["1min"],
        equity=10_000.0,
        params={},
    )
    await config_store.seed_algo_config(
        name="dup_algo",
        strategy_id="rsi_mean_reversion",  # changed
        warmup_candles=100,
        candle_intervals=["5min"],
        equity=5_000.0,
        params={},
    )
    async with get_session(engine) as s:
        cfg = await s.get(AlgoConfigModel, "dup_algo")
    assert cfg is not None
    assert cfg.strategy_id == "ema_crossover"  # original preserved


async def test_upsert_algo_state_insert_then_update(
    engine: AsyncEngine, config_store: ConfigStore
) -> None:
    """First call inserts; second call updates the existing row."""
    from trading.core.models import AlgoState as AlgoStateModel

    await config_store.upsert_algo_state("my:INFY", {"bars_seen": 1})
    async with get_session(engine) as s:
        state = await s.get(AlgoStateModel, "my:INFY")
    assert state is not None
    assert json.loads(state.state)["bars_seen"] == 1

    await config_store.upsert_algo_state("my:INFY", {"bars_seen": 42})
    async with get_session(engine) as s:
        state = await s.get(AlgoStateModel, "my:INFY")
    assert state is not None
    assert json.loads(state.state)["bars_seen"] == 42


@pytest.fixture
def chart_store(engine: AsyncEngine) -> ChartStore:
    return ChartStore(build_session_factory(engine))


# ---------------------------------------------------------------------------
# ChartStore
# ---------------------------------------------------------------------------


async def test_chart_store_get_chart_names_returns_logged_chart(
    engine: AsyncEngine, chart_store: ChartStore
) -> None:
    """Covers lines 81-94: get_chart_names() returns chart names after log_indicator()."""
    ts = datetime.now(UTC)
    await chart_store.log_indicator(
        algo_name="algo1",
        symbol="INFY",
        interval="1min",
        chart="price",
        series="vwap",
        ts=ts,
        value=1500.0,
        session_id=None,
    )
    since = ts - timedelta(seconds=1)
    names = await chart_store.get_chart_names("algo1", since, session_id=None)
    assert "price" in names


async def test_chart_store_get_indicator_series_returns_both_series(
    engine: AsyncEngine, chart_store: ChartStore
) -> None:
    """Covers lines 104-124: get_indicator_series() returns dict of series."""
    ts1 = datetime.now(UTC)
    ts2 = ts1 + timedelta(seconds=1)
    await chart_store.log_indicator(
        algo_name="algo2", symbol="INFY", interval="1min",
        chart="oscillators", series="rsi", ts=ts1, value=55.0, session_id=None,
    )
    await chart_store.log_indicator(
        algo_name="algo2", symbol="INFY", interval="1min",
        chart="oscillators", series="macd", ts=ts2, value=1.2, session_id=None,
    )
    since = ts1 - timedelta(seconds=1)
    result = await chart_store.get_indicator_series("algo2", "oscillators", since, session_id=None)
    assert "rsi" in result
    assert "macd" in result
    assert result["rsi"][0]["value"] == 55.0
    assert result["macd"][0]["value"] == 1.2


async def test_chart_store_get_indicator_series_filters_by_session_id(
    engine: AsyncEngine, chart_store: ChartStore
) -> None:
    """Covers session_id filtering in get_indicator_series()."""
    ts = datetime.now(UTC)
    await chart_store.log_indicator(
        algo_name="algo3", symbol="TCS", interval="5min",
        chart="price", series="ema", ts=ts, value=3000.0, session_id="sess-A",
    )
    await chart_store.log_indicator(
        algo_name="algo3", symbol="TCS", interval="5min",
        chart="price", series="ema", ts=ts, value=3001.0, session_id="sess-B",
    )
    since = ts - timedelta(seconds=1)
    result_a = await chart_store.get_indicator_series("algo3", "price", since, session_id="sess-A")
    result_b = await chart_store.get_indicator_series("algo3", "price", since, session_id="sess-B")
    assert len(result_a.get("ema", [])) == 1
    assert result_a["ema"][0]["value"] == 3000.0
    assert result_b["ema"][0]["value"] == 3001.0


# ---------------------------------------------------------------------------
# CandleDataStore — line 45: early return when rows is empty
# ---------------------------------------------------------------------------


async def test_candle_store_save_empty_rows_is_noop(engine: AsyncEngine) -> None:
    """Covers line 45: save_candles() returns early when rows is empty."""
    from trading.storage.stores.candle import CandleDataStore

    sf = build_session_factory(engine)
    store = CandleDataStore(sf)
    # Should not raise and should be a no-op
    await store.save_candles([])


async def test_get_algo_configs_with_state(engine: AsyncEngine, config_store: ConfigStore) -> None:
    from trading.core.models import AlgoConfig as AlgoConfigModel
    from trading.core.models import AlgoState as AlgoStateModel

    async with get_session(engine) as s:
        s.add(
            AlgoConfigModel(
                name="cfg1",
                strategy_id="ema_crossover",
                warmup_candles=200,
                candle_intervals=json.dumps(["1min"]),
                equity=10_000.0,
                params=json.dumps({"fast": 9}),
            )
        )
        s.add(AlgoStateModel(name="cfg1", state=json.dumps({"bars_seen": 10})))
        s.add(
            AlgoConfigModel(
                name="cfg2",
                strategy_id="rsi_mean_reversion",
                warmup_candles=100,
                candle_intervals=json.dumps(["5min"]),
                equity=5_000.0,
                params=json.dumps({}),
            )
        )
        # cfg2 has no AlgoState row

    results = await config_store.get_algo_configs_with_state()
    assert len(results) == 2
    by_name = {r["name"]: r for r in results}
    assert by_name["cfg1"]["state"]["bars_seen"] == 10
    assert by_name["cfg2"]["state"] == {}
    assert by_name["cfg2"]["updated_at"] is None
