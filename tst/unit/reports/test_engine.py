"""Tests for reports/engine.py — fetch_report_data() and _find_db_url()."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading.reports.engine import fetch_report_data

NOW = datetime.now(UTC)
START = NOW - timedelta(hours=1)
END = NOW + timedelta(hours=1)


def _make_session_factory(
    signals=None,
    decisions=None,
    audit_logs=None,
    heartbeats=None,
    algo_configs=None,
    nifty_benchmark=None,
):
    """Build a mock session_factory that patches all report fetch functions."""
    signals = signals or []
    decisions = decisions or []
    audit_logs = audit_logs or []
    heartbeats = heartbeats or []
    algo_configs = algo_configs or []

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    session_factory = MagicMock(return_value=session)

    return session_factory, {
        "signals": signals,
        "decisions": decisions,
        "audit_logs": audit_logs,
        "heartbeats": heartbeats,
        "algo_configs": algo_configs,
        "nifty_benchmark": nifty_benchmark,
    }


@pytest.mark.anyio
async def test_fetch_report_data_empty_returns_zeros():
    """Empty DB → all funnel counts are 0, pnl is 0."""
    sf, mocks = _make_session_factory()
    with (
        patch("trading.reports.engine.fetch_signals", AsyncMock(return_value=mocks["signals"])),
        patch("trading.reports.engine.fetch_decisions", AsyncMock(return_value=mocks["decisions"])),
        patch("trading.reports.engine.fetch_audit_logs", AsyncMock(return_value=mocks["audit_logs"])),
        patch("trading.reports.engine.fetch_heartbeats", AsyncMock(return_value=mocks["heartbeats"])),
        patch("trading.reports.engine.fetch_algo_configs", AsyncMock(return_value=mocks["algo_configs"])),
        patch("trading.reports.engine.fetch_nifty_benchmark", AsyncMock(return_value=None)),
    ):
        result = await fetch_report_data(START, END, sf)

    funnel = result["signal_funnel"]
    assert funnel["signals_generated"] == 0
    assert funnel["signals_accepted"] == 0
    assert funnel["signals_rejected"] == 0
    assert funnel["acceptance_rate"] == 0.0

    order_funnel = result["order_funnel"]
    assert order_funnel["placed"] == 0
    assert order_funnel["filled"] == 0
    assert order_funnel["fill_rate"] == 0.0

    pnl = result["pnl_summary"]
    assert pnl["gross"] == 0
    assert pnl["net"] == 0

    assert result["benchmark"] is None


@pytest.mark.anyio
async def test_fetch_report_data_signal_funnel_counts():
    """Decision log entries drive signal funnel counts."""
    def _decision(step: str, context: str | None = None):
        d = MagicMock()
        d.step = step
        d.context = context
        return d

    decisions = [
        _decision("SIGNAL_GENERATED"),
        _decision("SIGNAL_GENERATED"),
        _decision("SIGNAL_ACCEPTED"),
        _decision("SIGNAL_REJECTED", '{"reason": "AFTER_CUTOFF"}'),
        _decision("CANDLE_EMITTED"),
    ]

    sf, mocks = _make_session_factory(decisions=decisions)
    with (
        patch("trading.reports.engine.fetch_signals", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_decisions", AsyncMock(return_value=decisions)),
        patch("trading.reports.engine.fetch_audit_logs", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_heartbeats", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_algo_configs", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_nifty_benchmark", AsyncMock(return_value=None)),
    ):
        result = await fetch_report_data(START, END, sf)

    funnel = result["signal_funnel"]
    assert funnel["signals_generated"] == 2
    assert funnel["signals_accepted"] == 1
    assert funnel["signals_rejected"] == 1
    assert funnel["candles_emitted"] == 1
    assert funnel["acceptance_rate"] == pytest.approx(0.5)
    assert "AFTER_CUTOFF" in funnel["rejection_reasons"]


@pytest.mark.anyio
async def test_fetch_report_data_order_funnel():
    """Filled/rejected orders drive order funnel counts."""
    from trading.core.schemas import OrderStatus
    from decimal import Decimal

    def _filled_order(qty=10, price=100.0):
        o = MagicMock()
        o.status = OrderStatus.FILLED.value
        o.qty = qty
        o.avg_price = Decimal(str(price))
        return o

    def _rejected_order():
        o = MagicMock()
        o.status = OrderStatus.REJECTED.value
        o.qty = 5
        o.avg_price = Decimal("100.0")
        return o

    sig = MagicMock()
    sig.strategy_id = "ema"
    sig.symbol = "INFY"
    sig.side = "BUY"
    sig.orders = [_filled_order(), _rejected_order()]

    sf, _ = _make_session_factory(signals=[sig])
    with (
        patch("trading.reports.engine.fetch_signals", AsyncMock(return_value=[sig])),
        patch("trading.reports.engine.fetch_decisions", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_audit_logs", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_heartbeats", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_algo_configs", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_nifty_benchmark", AsyncMock(return_value=None)),
    ):
        result = await fetch_report_data(START, END, sf)

    order_funnel = result["order_funnel"]
    assert order_funnel["placed"] == 2
    assert order_funnel["filled"] == 1
    assert order_funnel["rejected"] == 1
    assert order_funnel["fill_rate"] == pytest.approx(0.5)


@pytest.mark.anyio
async def test_fetch_report_data_trades_by_symbol():
    """BUY and SELL orders are bucketed by symbol."""
    from trading.core.schemas import OrderStatus
    from decimal import Decimal

    def _order(status, qty, price):
        o = MagicMock()
        o.status = status
        o.qty = qty
        o.avg_price = Decimal(str(price))
        return o

    buy_sig = MagicMock()
    buy_sig.strategy_id = "ema"
    buy_sig.symbol = "INFY"
    buy_sig.side = "BUY"
    buy_sig.orders = [_order(OrderStatus.FILLED.value, 10, 100.0)]

    sell_sig = MagicMock()
    sell_sig.strategy_id = "ema"
    sell_sig.symbol = "INFY"
    sell_sig.side = "SELL"
    sell_sig.orders = [_order(OrderStatus.FILLED.value, 10, 120.0)]

    sf, _ = _make_session_factory()
    with (
        patch("trading.reports.engine.fetch_signals", AsyncMock(return_value=[buy_sig, sell_sig])),
        patch("trading.reports.engine.fetch_decisions", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_audit_logs", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_heartbeats", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_algo_configs", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_nifty_benchmark", AsyncMock(return_value=None)),
    ):
        result = await fetch_report_data(START, END, sf)

    trades = result["trades_by_symbol"]
    assert len(trades) == 1
    infy = trades[0]
    assert infy["symbol"] == "INFY"
    assert infy["buys"] == 1
    assert infy["sells"] == 1
    assert infy["volume"] == 20


@pytest.mark.anyio
async def test_fetch_report_data_heartbeat_system_health():
    """Heartbeat rows are surfaced in system_health with stale flag."""
    fresh_hb = MagicMock()
    fresh_hb.module = "kite_ingestor"
    fresh_hb.last_seen = datetime.now(UTC)

    stale_hb = MagicMock()
    stale_hb.module = "candle_aggregator"
    stale_hb.last_seen = datetime(2020, 1, 1, tzinfo=UTC)

    sf, _ = _make_session_factory()
    with (
        patch("trading.reports.engine.fetch_signals", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_decisions", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_audit_logs", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_heartbeats", AsyncMock(return_value=[fresh_hb, stale_hb])),
        patch("trading.reports.engine.fetch_algo_configs", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_nifty_benchmark", AsyncMock(return_value=None)),
    ):
        result = await fetch_report_data(START, END, sf)

    health = {h["module"]: h for h in result["system_health"]}
    assert health["kite_ingestor"]["stale"] is False
    assert health["candle_aggregator"]["stale"] is True


@pytest.mark.anyio
async def test_fetch_report_data_heartbeat_naive_datetime_handled():
    """Naive datetimes (no tzinfo) in heartbeat are handled without crashing."""
    hb = MagicMock()
    hb.module = "test_module"
    hb.last_seen = datetime(2024, 1, 1, 10, 0, 0)  # naive, no tzinfo

    sf, _ = _make_session_factory()
    with (
        patch("trading.reports.engine.fetch_signals", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_decisions", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_audit_logs", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_heartbeats", AsyncMock(return_value=[hb])),
        patch("trading.reports.engine.fetch_algo_configs", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_nifty_benchmark", AsyncMock(return_value=None)),
    ):
        result = await fetch_report_data(START, END, sf)

    assert len(result["system_health"]) == 1
    assert result["system_health"][0]["stale"] is True


@pytest.mark.anyio
async def test_fetch_report_data_with_nifty_benchmark():
    """Nifty benchmark is surfaced when provided."""
    nifty = {"open": 21000.0, "close": 21210.0, "pct_return": 1.0}

    algo_cfg = {
        "name": "default",
        "strategy_id": "ema",
        "equity": 100_000.0,
        "enabled": True,
    }

    sf, _ = _make_session_factory()
    with (
        patch("trading.reports.engine.fetch_signals", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_decisions", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_audit_logs", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_heartbeats", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_algo_configs", AsyncMock(return_value=[algo_cfg])),
        patch("trading.reports.engine.fetch_nifty_benchmark", AsyncMock(return_value=nifty)),
    ):
        result = await fetch_report_data(START, END, sf)

    b = result["benchmark"]
    assert b is not None
    assert b["nifty_open"] == pytest.approx(21000.0)
    assert b["nifty_close"] == pytest.approx(21210.0)
    assert b["pct_return"] == pytest.approx(1.0)


@pytest.mark.anyio
async def test_fetch_report_data_rejection_with_malformed_context():
    """Malformed JSON in decision context does not crash fetch_report_data."""
    def _decision(step, context=None):
        d = MagicMock()
        d.step = step
        d.context = context
        return d

    decisions = [
        _decision("SIGNAL_REJECTED", "not valid json {{{"),
    ]

    sf, _ = _make_session_factory()
    with (
        patch("trading.reports.engine.fetch_signals", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_decisions", AsyncMock(return_value=decisions)),
        patch("trading.reports.engine.fetch_audit_logs", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_heartbeats", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_algo_configs", AsyncMock(return_value=[])),
        patch("trading.reports.engine.fetch_nifty_benchmark", AsyncMock(return_value=None)),
    ):
        result = await fetch_report_data(START, END, sf)

    # UNKNOWN reason counted, no crash
    assert result["signal_funnel"]["rejection_reasons"].get("UNKNOWN", 0) >= 1


# ---------------------------------------------------------------------------
# _find_db_url
# ---------------------------------------------------------------------------


def test_find_db_url_converts_postgresql_prefix(monkeypatch) -> None:
    from trading.reports.engine import _find_db_url

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    result = _find_db_url()
    assert result == "postgresql+asyncpg://user:pass@localhost/db"


def test_find_db_url_converts_postgres_prefix(monkeypatch) -> None:
    from trading.reports.engine import _find_db_url

    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@localhost/db")
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    result = _find_db_url()
    assert result == "postgresql+asyncpg://user:pass@localhost/db"


def test_find_db_url_returns_asyncpg_url_unchanged(monkeypatch) -> None:
    from trading.reports.engine import _find_db_url

    url = "postgresql+asyncpg://user:pass@localhost/db"
    monkeypatch.setenv("DATABASE_URL", url)
    result = _find_db_url()
    assert result == url


def test_find_db_url_uses_postgres_url_env(monkeypatch) -> None:
    from trading.reports.engine import _find_db_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://host/mydb")
    result = _find_db_url()
    assert result == "postgresql+asyncpg://host/mydb"


def test_find_db_url_exits_when_no_env(monkeypatch) -> None:
    from unittest.mock import patch
    from trading.reports.engine import _find_db_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    # Prevent load_dotenv from loading from an actual .env file in the repo
    with patch("trading.reports.engine.load_dotenv"):
        with pytest.raises(SystemExit):
            _find_db_url()
