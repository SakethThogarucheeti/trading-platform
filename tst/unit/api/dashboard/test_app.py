"""Tests for monitoring/dashboard/app.py and component.py — mocked DB."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.clock import SimulatedClock
from trading.api.app import build_app

# ---------------------------------------------------------------------------
# Helpers — build a mock session_factory
# ---------------------------------------------------------------------------


def _mock_sf(scalars_return=None, fetchall_return=None, all_return=None):
    """
    Build a minimal mock async_sessionmaker whose sessions return
    pre-defined results from execute().scalars().all() or execute().fetchall().
    """
    mock_result = MagicMock()
    if scalars_return is not None:
        mock_result.scalars.return_value.all.return_value = scalars_return
    if fetchall_return is not None:
        mock_result.fetchall.return_value = fetchall_return
    if all_return is not None:
        mock_result.all.return_value = all_return

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_sf = MagicMock(spec=async_sessionmaker)
    mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_sf


async def _client(sf, clock=None):
    """Return an httpx AsyncClient backed by the FastAPI app (in-process)."""
    clock = clock or SimulatedClock()
    app = build_app(sf, clock)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET /api/sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sessions_empty():
    sf = _mock_sf(fetchall_return=[])
    async with await _client(sf) as client:
        resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_sessions_returns_session_ids():
    sf = _mock_sf(fetchall_return=[("session_1",), ("session_2",)])
    async with await _client(sf) as client:
        resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json() == ["session_1", "session_2"]


# ---------------------------------------------------------------------------
# GET /api/positions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_positions_empty():
    sf = _mock_sf(scalars_return=[])
    async with await _client(sf) as client:
        resp = await client.get("/api/positions")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_positions_with_data():
    pos = MagicMock()
    pos.symbol = "INFY"
    pos.instrument_type = "EQUITY"
    pos.net_qty = 10
    pos.avg_price = 1500.0
    pos.updated_at = datetime(2025, 1, 6, 9, 30, tzinfo=UTC)

    sf = _mock_sf(scalars_return=[pos])
    async with await _client(sf) as client:
        resp = await client.get("/api/positions")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["symbol"] == "INFY"
    assert data[0]["net_qty"] == 10


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_empty():
    sf = _mock_sf(scalars_return=[])
    async with await _client(sf) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_health_fresh_heartbeat_shows_ok():
    clock = SimulatedClock()
    now = datetime(2025, 1, 6, 9, 30, tzinfo=UTC)
    clock.advance(now)

    hb = MagicMock()
    hb.module = "candle_aggregator"
    hb.last_seen = now

    sf = _mock_sf(scalars_return=[hb])
    async with await _client(sf, clock=clock) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()[0]["stale"] is False


@pytest.mark.asyncio
async def test_health_stale_heartbeat_shows_stale():
    from datetime import timedelta

    clock = SimulatedClock()
    now = datetime(2025, 1, 6, 9, 30, tzinfo=UTC)
    clock.advance(now)

    hb = MagicMock()
    hb.module = "risk_registry"
    hb.last_seen = now - timedelta(seconds=60)

    sf = _mock_sf(scalars_return=[hb])
    async with await _client(sf, clock=clock) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()[0]["stale"] is True


# ---------------------------------------------------------------------------
# GET /api/signals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signals_empty():
    sf = _mock_sf(scalars_return=[])
    async with await _client(sf) as client:
        resp = await client.get("/api/signals")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_signals_with_data():
    row = MagicMock()
    row.created_at = datetime(2025, 1, 6, 9, 30, tzinfo=UTC)
    row.symbol = "INFY"
    row.algo_name = "test_algo"
    row.step = "SIGNAL_ACCEPTED"
    row.context = json.dumps({"reason": "EMA crossover"})

    sf = _mock_sf(scalars_return=[row])
    async with await _client(sf) as client:
        resp = await client.get("/api/signals")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["symbol"] == "INFY"
    assert data[0]["step"] == "SIGNAL_ACCEPTED"


# ---------------------------------------------------------------------------
# GET /api/candles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candles_empty():
    sf = _mock_sf(scalars_return=[])
    async with await _client(sf) as client:
        resp = await client.get("/api/candles?symbol=INFY&interval=15min&limit=10")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_candles_returns_ohlcv():
    from decimal import Decimal

    candle = MagicMock()
    candle.ts = datetime(2025, 1, 6, 9, 15, tzinfo=UTC)
    candle.open = Decimal("1500.00")
    candle.high = Decimal("1510.00")
    candle.low = Decimal("1495.00")
    candle.close = Decimal("1505.00")
    candle.volume = 10000

    sf = _mock_sf(scalars_return=[candle])
    async with await _client(sf) as client:
        resp = await client.get("/api/candles?symbol=INFY&interval=15min")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["close"] == pytest.approx(1505.0)
    assert "ts" in data[0]


# ---------------------------------------------------------------------------
# GET /api/ticks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ticks_empty():
    sf = _mock_sf(scalars_return=[])
    async with await _client(sf) as client:
        resp = await client.get("/api/ticks?symbol=INFY")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/pnl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pnl_empty():
    sf = _mock_sf(all_return=[])
    with patch("trading.reports.fetch.fetch_nifty_benchmark", new=AsyncMock(return_value=None)):
        async with await _client(sf) as client:
            resp = await client.get("/api/pnl")
    assert resp.status_code == 200
    data = resp.json()
    assert data["points"] == []
    assert data["summary"]["gross"] == 0.0
    assert data["summary"]["net"] == 0.0
    assert data["summary"]["nifty_pct"] is None


# ---------------------------------------------------------------------------
# GET /api/algos — mocks Repository.get_algo_configs_with_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_algos_empty():
    sf = _mock_sf(scalars_return=[])
    with patch("trading.storage.stores.config.ConfigStore.get_algo_configs_with_state", new=AsyncMock(return_value=[])):
        async with await _client(sf) as client:
            resp = await client.get("/api/algos")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_algos_with_data():
    algo = {
        "name": "my_algo",
        "strategy_id": "ema_crossover",
        "warmup_candles": 200,
        "params": {"fast": 9, "slow": 21},
        "state": {
            "bars_seen": 50,
            "warmup_candles": 200,
            "warmup_complete": False,
            "bars_remaining": 150,
            "last_signal_at": None,
        },
    }
    sf = _mock_sf(scalars_return=[])
    with patch("trading.storage.stores.config.ConfigStore.get_algo_configs_with_state", new=AsyncMock(return_value=[algo])):
        async with await _client(sf) as client:
            resp = await client.get("/api/algos")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["name"] == "my_algo"
    assert data[0]["strategy_id"] == "ema_crossover"


# ---------------------------------------------------------------------------
# ApiServer component — mock uvicorn.Server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_server_setup_creates_uvicorn_server():
    from unittest.mock import patch

    from trading.api.server import ApiServer

    sf = _mock_sf(scalars_return=[])
    server = ApiServer(session_factory=sf, host="127.0.0.1", port=8999)

    mock_uvicorn_server = AsyncMock()
    mock_uvicorn_server.serve = AsyncMock(return_value=None)

    with patch("uvicorn.Server", return_value=mock_uvicorn_server):
        with patch("uvicorn.Config"):
            await server._setup()

    assert server._server is mock_uvicorn_server


@pytest.mark.asyncio
async def test_dashboard_server_teardown_sets_should_exit():
    from trading.api.server import ApiServer

    sf = _mock_sf(scalars_return=[])
    server = ApiServer(session_factory=sf)

    mock_uvicorn_server = MagicMock()
    mock_uvicorn_server.should_exit = False
    server._server = mock_uvicorn_server

    await server._teardown()

    assert mock_uvicorn_server.should_exit is True


@pytest.mark.asyncio
async def test_dashboard_server_teardown_noop_when_no_server():
    from trading.api.server import ApiServer

    sf = _mock_sf(scalars_return=[])
    server = ApiServer(session_factory=sf)
    server._server = None

    # Should not raise
    await server._teardown()


# ---------------------------------------------------------------------------
# GET /api/pnl?algo_name= — per-algo filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pnl_with_algo_name_filter():
    """When algo_name is passed, Signal.algo_name filter is applied."""
    sf = _mock_sf(all_return=[])
    with patch("trading.reports.fetch.fetch_nifty_benchmark", new=AsyncMock(return_value=None)):
        async with await _client(sf) as client:
            resp = await client.get("/api/pnl?algo_name=ema_crossover")
    assert resp.status_code == 200
    data = resp.json()
    assert data["points"] == []
    assert data["summary"]["gross"] == 0.0


@pytest.mark.asyncio
async def test_pnl_with_signal_data_computes_summary():
    """P&L endpoint sums gross/net across filled orders."""
    from decimal import Decimal

    order = MagicMock()
    order.avg_price = Decimal("1500")
    order.qty = 10
    order.created_at = datetime(2025, 1, 6, 9, 30, tzinfo=UTC)

    signal = MagicMock()
    signal.side = "SELL"
    signal.symbol = "INFY"
    signal.signal_type = "ENTRY"

    sf = _mock_sf(all_return=[(order, signal)])
    with patch("trading.reports.fetch.fetch_nifty_benchmark", new=AsyncMock(return_value=None)):
        async with await _client(sf) as client:
            resp = await client.get("/api/pnl")
    assert resp.status_code == 200
    data = resp.json()
    # SELL 10 @ 1500 = +15000 gross
    assert data["summary"]["gross"] == pytest.approx(15000.0)
    assert data["summary"]["net"] < data["summary"]["gross"]  # costs deducted


# ---------------------------------------------------------------------------
# GET /api/pnl/by-algo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pnl_by_algo_empty():
    sf = _mock_sf(all_return=[])
    async with await _client(sf) as client:
        resp = await client.get("/api/pnl/by-algo")
    assert resp.status_code == 200
    assert resp.json() == {}


@pytest.mark.asyncio
async def test_pnl_by_algo_groups_by_algo_name():
    from decimal import Decimal

    order_a = MagicMock()
    order_a.avg_price = Decimal("1000")
    order_a.qty = 5
    order_a.created_at = datetime(2025, 1, 6, 9, 30, tzinfo=UTC)

    signal_a = MagicMock()
    signal_a.side = "SELL"
    signal_a.algo_name = "algo_a"

    order_b = MagicMock()
    order_b.avg_price = Decimal("2000")
    order_b.qty = 3
    order_b.created_at = datetime(2025, 1, 6, 10, 0, tzinfo=UTC)

    signal_b = MagicMock()
    signal_b.side = "BUY"
    signal_b.algo_name = "algo_b"

    sf = _mock_sf(all_return=[(order_a, signal_a), (order_b, signal_b)])
    async with await _client(sf) as client:
        resp = await client.get("/api/pnl/by-algo")
    assert resp.status_code == 200
    data = resp.json()
    assert "algo_a" in data
    assert "algo_b" in data
    # algo_a: SELL 5 @ 1000 = +5000 gross
    assert data["algo_a"]["gross"] == pytest.approx(5000.0)
    # algo_b: BUY 3 @ 2000 = -6000 gross
    assert data["algo_b"]["gross"] == pytest.approx(-6000.0)


@pytest.mark.asyncio
async def test_pnl_by_algo_null_algo_name_grouped_as_default():
    from decimal import Decimal

    order = MagicMock()
    order.avg_price = Decimal("500")
    order.qty = 2
    order.created_at = datetime(2025, 1, 6, 9, 30, tzinfo=UTC)

    signal = MagicMock()
    signal.side = "SELL"
    signal.algo_name = None  # no algo_name set

    sf = _mock_sf(all_return=[(order, signal)])
    async with await _client(sf) as client:
        resp = await client.get("/api/pnl/by-algo")
    assert resp.status_code == 200
    data = resp.json()
    assert "default" in data
    assert data["default"]["gross"] == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# GET /api/ping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_returns_ok():
    sf = _mock_sf(scalars_return=[])
    async with await _client(sf) as client:
        resp = await client.get("/api/ping")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# GET /api/settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_returns_candle_intervals():
    sf = _mock_sf(scalars_return=[])
    app = build_app(sf, candle_intervals=["1min", "5min"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["candle_intervals"] == ["1min", "5min"]


# ---------------------------------------------------------------------------
# GET /api/signals?algo_name=
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signals_with_algo_name_filter():
    """algo_name query param is forwarded and endpoint returns filtered rows."""
    sf = _mock_sf(scalars_return=[])
    async with await _client(sf) as client:
        resp = await client.get("/api/signals?algo_name=ema_crossover")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/charts?algo_name=
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_charts_with_algo_name_filter():
    """charts endpoint with algo_name restricts to that algo's series."""
    sf = _mock_sf(scalars_return=[], fetchall_return=[])
    with patch("trading.storage.stores.chart.ChartStore.get_chart_names", new=AsyncMock(return_value=[])):
        async with await _client(sf) as client:
            resp = await client.get("/api/charts?algo_name=ema_crossover")
    assert resp.status_code == 200
    assert resp.json() == {}
