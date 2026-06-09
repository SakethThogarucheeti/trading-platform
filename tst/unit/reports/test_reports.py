"""Tests for reports/pnl.py, reports/fetch.py, and reports/render.py"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.app.database import build_session_factory, get_session, init_db
from trading.core.models import (
    AlgoConfig,
    AlgoState,
    Heartbeat,
    Signal,
)
from trading.core.schemas import OrderStatus
from trading.reports.fetch import (
    AlgoConfigSnapshot,
    NiftyBenchmark,
    fetch_algo_configs,
    fetch_audit_logs,
    fetch_decisions,
    fetch_heartbeats,
    fetch_signals,
)
from trading.reports.pnl import compute_pnl
from trading.reports.render import (
    hr,
    pnl_str,
    print_strategy_section,
    print_system_section,
    row,
    section,
    subsection,
)

NOW = datetime.now(UTC)
START = NOW - timedelta(hours=1)
END = NOW + timedelta(hours=1)


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


# ---------------------------------------------------------------------------
# pnl.py — compute_pnl
# ---------------------------------------------------------------------------


def _mock_signal(strategy_id: str, symbol: str, side: str, qty: int, price: float) -> MagicMock:
    order = MagicMock()
    order.status = OrderStatus.FILLED.value
    order.qty = qty
    order.avg_price = Decimal(str(price))

    sig = MagicMock()
    sig.strategy_id = strategy_id
    sig.symbol = symbol
    sig.side = side
    sig.orders = [order]
    return sig


def test_compute_pnl_simple_buy_sell() -> None:
    # Buy 10 @ 100, sell 10 @ 120 → profit = 200
    signals = [
        _mock_signal("ema", "INFY", "BUY", 10, 100.0),
        _mock_signal("ema", "INFY", "SELL", 10, 120.0),
    ]
    result = compute_pnl(signals)
    assert "ema::INFY" in result
    assert result["ema::INFY"]["realized"] == pytest.approx(200.0)
    assert result["ema::INFY"]["open_qty"] == 0.0


def test_compute_pnl_partial_close() -> None:
    # Buy 10 @ 100, sell 5 @ 120 → realized = 100, open_qty = 5
    signals = [
        _mock_signal("ema", "INFY", "BUY", 10, 100.0),
        _mock_signal("ema", "INFY", "SELL", 5, 120.0),
    ]
    result = compute_pnl(signals)
    assert result["ema::INFY"]["realized"] == pytest.approx(100.0)
    assert result["ema::INFY"]["open_qty"] == 5.0
    assert result["ema::INFY"]["open_avg"] == pytest.approx(100.0)


def test_compute_pnl_short_position() -> None:
    # Sell 10 @ 120, buy 10 @ 100 → profit = 200
    signals = [
        _mock_signal("ema", "INFY", "SELL", 10, 120.0),
        _mock_signal("ema", "INFY", "BUY", 10, 100.0),
    ]
    result = compute_pnl(signals)
    assert result["ema::INFY"]["realized"] == pytest.approx(200.0)


def test_compute_pnl_empty_signals() -> None:
    assert compute_pnl([]) == {}


def test_compute_pnl_no_filled_orders() -> None:
    order = MagicMock()
    order.status = OrderStatus.REJECTED.value
    sig = MagicMock()
    sig.strategy_id = "ema"
    sig.symbol = "INFY"
    sig.side = "BUY"
    sig.orders = [order]
    result = compute_pnl([sig])
    assert result == {}


def test_compute_pnl_partial_short_close() -> None:
    """Covers line 47: partial cover of a short position (matched < short_qty)."""
    # Sell 10 @ 100 (open short), buy 5 @ 90 (partial cover)
    sell_sig = _mock_signal("ema", "INFY", "SELL", 10, 100.0)
    buy_sig = _mock_signal("ema", "INFY", "BUY", 5, 90.0)
    result = compute_pnl([sell_sig, buy_sig])
    # 5 units covered: profit = 5 * (100 - 90) = 50
    assert result["ema::INFY"]["realized"] == pytest.approx(50.0)


def test_compute_pnl_multiple_symbols_independent() -> None:
    signals = [
        _mock_signal("ema", "INFY", "BUY", 5, 100.0),
        _mock_signal("ema", "INFY", "SELL", 5, 110.0),
        _mock_signal("ema", "TCS", "BUY", 2, 200.0),
        _mock_signal("ema", "TCS", "SELL", 2, 220.0),
    ]
    result = compute_pnl(signals)
    assert result["ema::INFY"]["realized"] == pytest.approx(50.0)
    assert result["ema::TCS"]["realized"] == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# fetch.py
# ---------------------------------------------------------------------------


async def test_fetch_signals_returns_empty_on_empty_db(engine: AsyncEngine) -> None:
    sf = build_session_factory(engine)
    async with sf() as session:
        result = await fetch_signals(session, START, END)
    assert result == []


async def test_fetch_signals_returns_signals_in_window(engine: AsyncEngine) -> None:
    async with get_session(engine) as s:
        s.add(
            Signal(
                id=uuid4(),
                strategy_id="ema",
                symbol="INFY",
                instrument_type="EQUITY",
                side="BUY",
                signal_type="ENTRY",
                stop_distance=Decimal("10"),
                created_at=NOW,
            )
        )

    sf = build_session_factory(engine)
    async with sf() as session:
        result = await fetch_signals(session, START, END)
    assert len(result) == 1


async def test_fetch_signals_excludes_outside_window(engine: AsyncEngine) -> None:
    old = NOW - timedelta(hours=3)
    async with get_session(engine) as s:
        s.add(
            Signal(
                id=uuid4(),
                strategy_id="ema",
                symbol="INFY",
                instrument_type="EQUITY",
                side="BUY",
                signal_type="ENTRY",
                stop_distance=Decimal("10"),
                created_at=old,
            )
        )

    sf = build_session_factory(engine)
    async with sf() as session:
        result = await fetch_signals(session, START, END)
    assert result == []


async def test_fetch_decisions_empty(engine: AsyncEngine) -> None:
    sf = build_session_factory(engine)
    async with sf() as session:
        result = await fetch_decisions(session, START, END)
    assert result == []


async def test_fetch_audit_logs_empty(engine: AsyncEngine) -> None:
    sf = build_session_factory(engine)
    async with sf() as session:
        result = await fetch_audit_logs(session, START, END)
    assert result == []


async def test_fetch_heartbeats_empty(engine: AsyncEngine) -> None:
    sf = build_session_factory(engine)
    async with sf() as session:
        result = await fetch_heartbeats(session)
    assert result == []


async def test_fetch_heartbeats_returns_all(engine: AsyncEngine) -> None:
    async with get_session(engine) as s:
        s.add(Heartbeat(module="kite_ingestor", last_seen=NOW))
        s.add(Heartbeat(module="candle_aggregator", last_seen=NOW))

    sf = build_session_factory(engine)
    async with sf() as session:
        result = await fetch_heartbeats(session)
    assert len(result) == 2


async def test_fetch_algo_configs_empty(engine: AsyncEngine) -> None:
    sf = build_session_factory(engine)
    async with sf() as session:
        result = await fetch_algo_configs(session)
    assert result == []


async def test_fetch_algo_configs_returns_config_with_state(engine: AsyncEngine) -> None:
    async with get_session(engine) as s:
        cfg = AlgoConfig(
            name="default",
            strategy_id="ema_crossover",
            warmup_candles=200,
            candle_intervals=json.dumps(["1min"]),
            equity=10000.0,
            params=json.dumps({"fast": 9}),
        )
        s.add(cfg)
        s.add(AlgoState(name="default", state=json.dumps({"bars_seen": 50})))

    sf = build_session_factory(engine)
    async with sf() as session:
        result = await fetch_algo_configs(session)

    assert len(result) == 1
    assert result[0].name == "default"
    assert result[0].state["bars_seen"] == 50


async def test_fetch_algo_configs_without_state(engine: AsyncEngine) -> None:
    async with get_session(engine) as s:
        s.add(
            AlgoConfig(
                name="solo",
                strategy_id="ema_crossover",
                warmup_candles=100,
                candle_intervals=json.dumps(["5min"]),
                equity=5000.0,
                params=json.dumps({}),
            )
        )

    sf = build_session_factory(engine)
    async with sf() as session:
        result = await fetch_algo_configs(session)

    assert result[0].state == {}


# ---------------------------------------------------------------------------
# render.py — primitive helpers
# ---------------------------------------------------------------------------


def test_hr_prints_line(capsys) -> None:
    hr("─")
    out = capsys.readouterr().out
    assert "─" * 70 in out


def test_section_prints_title(capsys) -> None:
    section("TEST SECTION")
    out = capsys.readouterr().out
    assert "TEST SECTION" in out


def test_subsection_prints_title(capsys) -> None:
    subsection("Sub Title")
    out = capsys.readouterr().out
    assert "Sub Title" in out


def test_row_prints_label_and_value(capsys) -> None:
    row("My Label", 42)
    out = capsys.readouterr().out
    assert "My Label" in out
    assert "42" in out


def test_pnl_str_positive() -> None:
    assert pnl_str(100.5) == "+100.50"


def test_pnl_str_negative() -> None:
    assert pnl_str(-50.0) == "-50.00"


def test_pnl_str_zero() -> None:
    assert pnl_str(0.0) == "+0.00"


# ---------------------------------------------------------------------------
# render.py — print_strategy_section
# ---------------------------------------------------------------------------


def test_print_strategy_section_empty(capsys) -> None:
    print_strategy_section(signals=[], decisions=[], algo_configs=[])
    out = capsys.readouterr().out
    assert "No filled orders" in out
    assert "No matched trades" in out
    assert "No algo configs" in out


def test_print_strategy_section_with_data(capsys) -> None:
    sig = _mock_signal("ema", "INFY", "BUY", 10, 100.0)

    d_gen = MagicMock()
    d_gen.step = "SIGNAL_GENERATED"
    d_gen.context = None

    d_acc = MagicMock()
    d_acc.step = "SIGNAL_ACCEPTED"
    d_acc.context = None

    d_rej = MagicMock()
    d_rej.step = "SIGNAL_REJECTED"
    d_rej.context = json.dumps({"reason": "AFTER_CUTOFF"})

    algo_cfg = AlgoConfigSnapshot(
        name="default",
        strategy_id="ema_crossover",
        equity=10000.0,
        enabled=True,
        warmup_candles=200,
        params={"fast": 9, "slow": 21},
        state={"bars_seen": 50, "warmup_complete": True},
    )

    print_strategy_section(
        signals=[sig],
        decisions=[d_gen, d_acc, d_rej],
        algo_configs=[algo_cfg],
    )
    out = capsys.readouterr().out
    assert "INFY" in out
    assert "AFTER_CUTOFF" in out
    assert "ema_crossover" in out


# ---------------------------------------------------------------------------
# render.py — print_system_section
# ---------------------------------------------------------------------------


def test_print_system_section_empty(capsys) -> None:
    print_system_section(decisions=[], audit_logs=[], heartbeats=[])
    out = capsys.readouterr().out
    assert "No heartbeat records" in out
    assert "No risk rejections" in out


def test_print_system_section_with_stale_heartbeat(capsys) -> None:
    hb = MagicMock()
    hb.module = "kite_ingestor"
    hb.last_seen = datetime(2020, 1, 1, tzinfo=UTC)  # very stale

    print_system_section(decisions=[], audit_logs=[], heartbeats=[hb])
    out = capsys.readouterr().out
    assert "STALE" in out


def test_print_system_section_with_ok_heartbeat(capsys) -> None:
    hb = MagicMock()
    hb.module = "kite_ingestor"
    hb.last_seen = datetime.now(UTC)  # fresh

    print_system_section(decisions=[], audit_logs=[], heartbeats=[hb])
    out = capsys.readouterr().out
    assert "OK" in out


def test_print_system_section_with_errors(capsys) -> None:
    entry = MagicMock()
    entry.level = "ERROR"
    entry.module = "risk_registry"
    entry.message = "something went wrong"
    entry.created_at = NOW

    print_system_section(decisions=[], audit_logs=[entry], heartbeats=[])
    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "something went wrong" in out


def test_print_system_section_with_risk_rejections(capsys) -> None:
    d = MagicMock()
    d.step = "SIGNAL_REJECTED"
    d.context = json.dumps({"reason": "CIRCUIT_OPEN"})
    d.algo_name = None

    print_system_section(decisions=[d], audit_logs=[], heartbeats=[])
    out = capsys.readouterr().out
    assert "CIRCUIT_OPEN" in out
    assert "Circuit breaker fired" in out


def test_print_strategy_section_sell_signal_coverage(capsys) -> None:
    """Covers the SELL branch and non-FILLED skip (lines 114, 120-121)."""
    # SELL signal with a FILLED order
    sig_sell = _mock_signal("ema", "TCS", "SELL", 5, 200.0)

    # Signal with a non-FILLED order — hits the `continue` at line 114
    non_filled_order = MagicMock()
    non_filled_order.status = OrderStatus.PLACED.value
    non_filled_order.qty = 5
    non_filled_order.avg_price = Decimal("200.0")
    sig_placed = MagicMock()
    sig_placed.strategy_id = "ema"
    sig_placed.symbol = "RELIANCE"
    sig_placed.side = "BUY"
    sig_placed.orders = [non_filled_order]

    print_strategy_section(signals=[sig_sell, sig_placed], decisions=[], algo_configs=[])
    out = capsys.readouterr().out
    assert "TCS" in out


def test_print_system_section_candle_emitted_per_algo(capsys) -> None:
    """Covers algo_candles breakdown when CANDLE_EMITTED decisions have algo_name
    (lines 199-204)."""
    d1 = MagicMock()
    d1.step = "CANDLE_EMITTED"
    d1.algo_name = "my_algo"
    d1.context = None

    d2 = MagicMock()
    d2.step = "CANDLE_EMITTED"
    d2.algo_name = "my_algo"
    d2.context = None

    print_system_section(decisions=[d1, d2], audit_logs=[], heartbeats=[])
    out = capsys.readouterr().out
    assert "my_algo" in out
    assert "Candles per algo" in out


def test_print_system_section_heartbeat_without_tzinfo(capsys) -> None:
    """Covers the tzinfo=None branch for heartbeat.last_seen (line 217)."""
    hb = MagicMock()
    hb.module = "candle_agg"
    hb.last_seen = datetime.now()  # naive datetime — no tzinfo

    print_system_section(decisions=[], audit_logs=[], heartbeats=[hb])
    out = capsys.readouterr().out
    assert "candle_agg" in out


def test_print_system_section_audit_truncation(capsys) -> None:
    """Covers the '... and N more' line when there are more than 10 errors (line 243)."""
    entries = []
    for i in range(12):
        entry = MagicMock()
        entry.level = "ERROR"
        entry.module = "mod"
        entry.message = f"error {i}"
        entry.created_at = NOW
        entries.append(entry)

    print_system_section(decisions=[], audit_logs=entries, heartbeats=[])
    out = capsys.readouterr().out
    assert "more" in out


# ---------------------------------------------------------------------------
# fetch.py — _safe_json with malformed JSON (lines 25-27)
# ---------------------------------------------------------------------------


def test_fetch_safe_json_with_malformed_json() -> None:
    """Covers lines 25-27: _safe_json returns {} for malformed JSON in fetch module."""
    from trading.reports.fetch import _safe_json

    result = _safe_json("not valid json {{")
    assert result == {}


def test_fetch_safe_json_with_none() -> None:
    """_safe_json returns {} for None input."""
    from trading.reports.fetch import _safe_json

    result = _safe_json(None)
    assert result == {}


def test_fetch_safe_json_with_empty_string() -> None:
    """_safe_json returns {} for empty string."""
    from trading.reports.fetch import _safe_json

    result = _safe_json("")
    assert result == {}


def test_fetch_safe_json_valid() -> None:
    """_safe_json parses valid JSON correctly."""
    from trading.reports.fetch import _safe_json

    result = _safe_json('{"key": "value"}')
    assert result == {"key": "value"}


# ---------------------------------------------------------------------------
# fetch.py — fetch_nifty_benchmark (lines 75-93)
# ---------------------------------------------------------------------------


async def test_fetch_nifty_benchmark_returns_none_when_no_candles(engine: AsyncEngine) -> None:
    """Covers lines 75-93: fetch_nifty_benchmark returns None when no Nifty 50 candles exist."""
    from trading.reports.fetch import fetch_nifty_benchmark

    sf = build_session_factory(engine)
    async with sf() as session:
        result = await fetch_nifty_benchmark(session, START, END)
    assert result is None


async def test_fetch_nifty_benchmark_returns_none_when_open_is_zero(engine: AsyncEngine) -> None:
    """Covers line 91: fetch_nifty_benchmark returns None when open_price == 0."""
    from decimal import Decimal

    from trading.core.models import Candle
    from trading.reports.fetch import fetch_nifty_benchmark

    ts = NOW - timedelta(minutes=5)
    async with get_session(engine) as s:
        s.add(
            Candle(
                symbol="NIFTY 50",
                interval="1min",
                ts=ts,
                open=Decimal("0"),  # open=0 → division by zero guard → return None
                high=Decimal("100"),
                low=Decimal("0"),
                close=Decimal("100"),
                volume=1000,
            )
        )

    sf = build_session_factory(engine)
    async with sf() as session:
        result = await fetch_nifty_benchmark(session, START, END)

    assert result is None


async def test_fetch_nifty_benchmark_returns_dict_when_candles_exist(engine: AsyncEngine) -> None:
    """Covers lines 84-93: fetch_nifty_benchmark returns open/close/pct_return dict."""
    from decimal import Decimal

    from trading.core.models import Candle
    from trading.reports.fetch import fetch_nifty_benchmark

    ts1 = NOW - timedelta(minutes=10)
    ts2 = NOW - timedelta(minutes=5)
    async with get_session(engine) as s:
        s.add(
            Candle(
                symbol="NIFTY 50",
                interval="1min",
                ts=ts1,
                open=Decimal("21000"),
                high=Decimal("21100"),
                low=Decimal("20900"),
                close=Decimal("21050"),
                volume=500000,
            )
        )
        s.add(
            Candle(
                symbol="NIFTY 50",
                interval="1min",
                ts=ts2,
                open=Decimal("21050"),
                high=Decimal("21200"),
                low=Decimal("21000"),
                close=Decimal("21150"),
                volume=600000,
            )
        )

    sf = build_session_factory(engine)
    async with sf() as session:
        result = await fetch_nifty_benchmark(session, START, END)

    assert result is not None
    assert result.open == pytest.approx(21000.0)
    assert result.close == pytest.approx(21150.0)
    assert result.pct_return is not None


# ---------------------------------------------------------------------------
# render.py — _safe_json with malformed JSON (lines 19, 22-24)
# ---------------------------------------------------------------------------


def test_render_safe_json_with_malformed_json() -> None:
    """Covers lines 19, 22-24: _safe_json returns {} for malformed JSON in render module."""
    from trading.reports.render import _safe_json

    result = _safe_json("{bad json")
    assert result == {}


def test_render_safe_json_with_none() -> None:
    """_safe_json returns {} for None."""
    from trading.reports.render import _safe_json

    assert _safe_json(None) == {}


# ---------------------------------------------------------------------------
# render.py — Nifty 50 benchmark section (lines 192-208)
# ---------------------------------------------------------------------------


def test_print_strategy_section_with_nifty_benchmark(capsys) -> None:
    """Covers lines 192-208: Nifty 50 benchmark section in print_strategy_section."""
    nifty = NiftyBenchmark(open=21000.0, close=21210.0, pct_return=1.0)

    algo_cfg = AlgoConfigSnapshot(
        name="default",
        strategy_id="ema_crossover",
        equity=100_000.0,
        enabled=True,
        warmup_candles=200,
        params={},
        state={"bars_seen": 50, "warmup_complete": True},
    )

    print_strategy_section(
        signals=[],
        decisions=[],
        algo_configs=[algo_cfg],
        nifty_benchmark=nifty,
    )
    out = capsys.readouterr().out
    assert "Nifty 50" in out
    assert "21,000.00" in out
    assert "1.00%" in out


def test_print_strategy_section_nifty_benchmark_with_pnl(capsys) -> None:
    """Covers the alpha comparison path (lines 199-212) when pnl_map is non-empty."""
    nifty = NiftyBenchmark(open=21000.0, close=21210.0, pct_return=1.0)

    # Create a signal that produces realized P&L
    sig = _mock_signal("ema", "INFY", "BUY", 10, 100.0)
    sell_sig = _mock_signal("ema", "INFY", "SELL", 10, 120.0)

    algo_cfg = AlgoConfigSnapshot(
        name="default",
        strategy_id="ema_crossover",
        equity=100_000.0,
        enabled=True,
        warmup_candles=200,
        params={},
        state={"bars_seen": 50, "warmup_complete": True},
    )

    print_strategy_section(
        signals=[sig, sell_sig],
        decisions=[],
        algo_configs=[algo_cfg],
        nifty_benchmark=nifty,
    )
    out = capsys.readouterr().out
    assert "Nifty 50" in out
