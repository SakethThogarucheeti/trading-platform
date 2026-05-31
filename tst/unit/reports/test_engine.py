"""Tests for reports/engine.py — fetch_report_data() and _find_db_url()."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading.reports.engine import fetch_report_data
from trading.reports.fetch import AlgoConfigSnapshot, NiftyBenchmark
from trading.reports.trades import FilledTrade

NOW = datetime.now(UTC)
START = NOW - timedelta(hours=1)
END = NOW + timedelta(hours=1)


def _make_session_factory():
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=session)


def _trade(symbol="INFY", side="BUY", qty=10, price=100.0, gross=None, cost=1.0, algo="default"):
    g = gross if gross is not None else (-qty * price if side == "BUY" else qty * price)
    t = MagicMock(spec=FilledTrade)
    t.symbol = symbol
    t.side = side
    t.qty = qty
    t.avg_price = price
    t.gross = g
    t.cost = cost
    t.net = g - cost
    t.algo_name = algo
    return t


def _decision(step, context=None):
    d = MagicMock()
    d.step = step
    d.context = context
    return d


def _algo_config(**kwargs):
    defaults = dict(name="default", strategy_id="ema", equity=100_000.0, enabled=True,
                    params={}, warmup_candles=50, state={})
    return AlgoConfigSnapshot(**(defaults | kwargs))


@contextmanager
def _patches(trades=None, decisions=None, heartbeats=None, algo_configs=None, nifty=None):
    with patch("trading.reports.engine.fetch_decisions", AsyncMock(return_value=decisions or [])), \
         patch("trading.reports.engine.fetch_audit_logs", AsyncMock(return_value=[])), \
         patch("trading.reports.engine.fetch_heartbeats", AsyncMock(return_value=heartbeats or [])), \
         patch("trading.reports.engine.fetch_algo_configs", AsyncMock(return_value=algo_configs or [])), \
         patch("trading.reports.engine.fetch_nifty_benchmark", AsyncMock(return_value=nifty)), \
         patch("trading.reports.trades.fetch_filled_trades", AsyncMock(return_value=trades or [])):
        yield


@pytest.mark.anyio
async def test_fetch_report_data_empty_returns_zeros():
    sf = _make_session_factory()
    with _patches():
        result = await fetch_report_data(START, END, sf)

    funnel = result.signal_funnel
    assert funnel.signals_generated == 0
    assert funnel.signals_accepted == 0
    assert funnel.signals_rejected == 0
    assert funnel.acceptance_rate == 0.0

    order_funnel = result.order_funnel
    assert order_funnel.placed == 0
    assert order_funnel.filled == 0
    assert order_funnel.fill_rate == 0.0

    pnl = result.pnl_summary
    assert pnl.gross == 0
    assert pnl.net == 0

    assert result.benchmark is None


@pytest.mark.anyio
async def test_fetch_report_data_signal_funnel_counts():
    decisions = [
        _decision("SIGNAL_GENERATED"),
        _decision("SIGNAL_GENERATED"),
        _decision("SIGNAL_ACCEPTED"),
        _decision("SIGNAL_REJECTED", '{"reason": "AFTER_CUTOFF"}'),
        _decision("CANDLE_EMITTED"),
    ]

    sf = _make_session_factory()
    with _patches(decisions=decisions):
        result = await fetch_report_data(START, END, sf)

    funnel = result.signal_funnel
    assert funnel.signals_generated == 2
    assert funnel.signals_accepted == 1
    assert funnel.signals_rejected == 1
    assert funnel.candles_emitted == 1
    assert funnel.acceptance_rate == pytest.approx(0.5)
    assert "AFTER_CUTOFF" in funnel.rejection_reasons


@pytest.mark.anyio
async def test_fetch_report_data_order_funnel():
    """filled count comes from fetch_filled_trades; placed from SIGNAL_ACCEPTED decisions."""
    decisions = [_decision("SIGNAL_ACCEPTED"), _decision("SIGNAL_ACCEPTED")]
    trades = [_trade(side="BUY")]

    sf = _make_session_factory()
    with _patches(decisions=decisions, trades=trades):
        result = await fetch_report_data(START, END, sf)

    order_funnel = result.order_funnel
    assert order_funnel.placed == 2
    assert order_funnel.filled == 1
    assert order_funnel.fill_rate == pytest.approx(0.5)


@pytest.mark.anyio
async def test_fetch_report_data_trades_by_symbol():
    buy = _trade(symbol="INFY", side="BUY", qty=10, price=100.0, gross=-1000.0)
    sell = _trade(symbol="INFY", side="SELL", qty=10, price=120.0, gross=1200.0)

    sf = _make_session_factory()
    with _patches(trades=[buy, sell]):
        result = await fetch_report_data(START, END, sf)

    trades_by_sym = result.trades_by_symbol
    assert len(trades_by_sym) == 1
    infy = trades_by_sym[0]
    assert infy.symbol == "INFY"
    assert infy.buys == 1
    assert infy.sells == 1
    assert infy.volume == 20


@pytest.mark.anyio
async def test_fetch_report_data_heartbeat_system_health():
    fresh_hb = MagicMock()
    fresh_hb.module = "kite_ingestor"
    fresh_hb.last_seen = datetime.now(UTC)

    stale_hb = MagicMock()
    stale_hb.module = "candle_aggregator"
    stale_hb.last_seen = datetime(2020, 1, 1, tzinfo=UTC)

    sf = _make_session_factory()
    with _patches(heartbeats=[fresh_hb, stale_hb]):
        result = await fetch_report_data(START, END, sf)

    health = {h.module: h for h in result.system_health}
    assert health["kite_ingestor"].stale is False
    assert health["candle_aggregator"].stale is True


@pytest.mark.anyio
async def test_fetch_report_data_heartbeat_naive_datetime_handled():
    hb = MagicMock()
    hb.module = "test_module"
    hb.last_seen = datetime(2024, 1, 1, 10, 0, 0)  # naive, no tzinfo

    sf = _make_session_factory()
    with _patches(heartbeats=[hb]):
        result = await fetch_report_data(START, END, sf)

    assert len(result.system_health) == 1
    assert result.system_health[0].stale is True


@pytest.mark.anyio
async def test_fetch_report_data_with_nifty_benchmark():
    nifty = NiftyBenchmark(open=21000.0, close=21210.0, pct_return=1.0)
    algo_cfg = _algo_config(equity=100_000.0, enabled=True)

    sf = _make_session_factory()
    with _patches(algo_configs=[algo_cfg], nifty=nifty):
        result = await fetch_report_data(START, END, sf)

    b = result.benchmark
    assert b is not None
    assert b.nifty_open == pytest.approx(21000.0)
    assert b.nifty_close == pytest.approx(21210.0)
    assert b.pct_return == pytest.approx(1.0)


@pytest.mark.anyio
async def test_fetch_report_data_rejection_with_malformed_context():
    decisions = [_decision("SIGNAL_REJECTED", "not valid json {{{")]

    sf = _make_session_factory()
    with _patches(decisions=decisions):
        result = await fetch_report_data(START, END, sf)

    assert result.signal_funnel.rejection_reasons.get("UNKNOWN", 0) >= 1


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
    from trading.reports.engine import _find_db_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with patch("trading.reports.engine.load_dotenv"):
        with pytest.raises(SystemExit):
            _find_db_url()
