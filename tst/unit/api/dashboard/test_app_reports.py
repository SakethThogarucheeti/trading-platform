"""Additional tests for /api/reports/* and auth endpoints."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.clock import SimulatedClock
from trading.api.dashboard.app import build_app


def _mock_sf(scalars_return=None, all_return=None):
    mock_result = MagicMock()
    if scalars_return is not None:
        mock_result.scalars.return_value.all.return_value = scalars_return
    if all_return is not None:
        mock_result.all.return_value = all_return

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_sf = MagicMock(spec=async_sessionmaker)
    mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_sf


# ---------------------------------------------------------------------------
# GET /api/reports/sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_report_sessions_empty_when_no_dir():
    sf = _mock_sf(scalars_return=[])
    nonexistent = Path(tempfile.gettempdir()) / "no_such_dir_xyz987"
    app = build_app(sf, results_dir=nonexistent)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/reports/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_report_sessions_returns_sessions_from_files():
    sf = _mock_sf(scalars_return=[])
    with tempfile.TemporaryDirectory() as tmpdir:
        results = Path(tmpdir)
        session_dir = results / "bt-test-001"
        session_dir.mkdir()
        report = {
            "session_id": "bt-test-001",
            "session_type": "backtest",
            "algo_name": "ema_crossover",
            "started_at": "2026-05-01T09:00:00Z",
            "finished_at": "2026-05-01T09:45:00Z",
        }
        (session_dir / "report.json").write_text(json.dumps(report))

        app = build_app(sf, results_dir=results)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/reports/sessions")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["session_id"] == "bt-test-001"
    assert data[0]["algo_name"] == "ema_crossover"


@pytest.mark.asyncio
async def test_get_report_sessions_skips_malformed_files():
    sf = _mock_sf(scalars_return=[])
    with tempfile.TemporaryDirectory() as tmpdir:
        results = Path(tmpdir)
        bad_dir = results / "bad-session"
        bad_dir.mkdir()
        (bad_dir / "report.json").write_text("not valid json{{{")

        app = build_app(sf, results_dir=results)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/reports/sessions")

    assert resp.status_code == 200
    assert resp.json() == []  # malformed file skipped silently


# ---------------------------------------------------------------------------
# GET /api/reports/{session_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_report_by_session_id_returns_data():
    sf = _mock_sf(scalars_return=[])
    with tempfile.TemporaryDirectory() as tmpdir:
        results = Path(tmpdir)
        session_dir = results / "bt-test-001"
        session_dir.mkdir()
        report = {"session_id": "bt-test-001", "session_type": "backtest", "final_equity": 112500}
        (session_dir / "report.json").write_text(json.dumps(report))

        app = build_app(sf, results_dir=results)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/reports/bt-test-001")

    assert resp.status_code == 200
    assert resp.json()["final_equity"] == 112500


@pytest.mark.asyncio
async def test_get_report_returns_404_when_missing():
    sf = _mock_sf(scalars_return=[])
    with tempfile.TemporaryDirectory() as tmpdir:
        app = build_app(sf, results_dir=Path(tmpdir))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/reports/no-such-session")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/reports/live
# ---------------------------------------------------------------------------


def _all_mocked():
    return {
        "trading.reports.engine.fetch_signals": AsyncMock(return_value=[]),
        "trading.reports.engine.fetch_decisions": AsyncMock(return_value=[]),
        "trading.reports.engine.fetch_audit_logs": AsyncMock(return_value=[]),
        "trading.reports.engine.fetch_heartbeats": AsyncMock(return_value=[]),
        "trading.reports.engine.fetch_algo_configs": AsyncMock(return_value=[]),
        "trading.reports.engine.fetch_nifty_benchmark": AsyncMock(return_value=None),
    }


@pytest.mark.asyncio
async def test_get_live_report_day_period():
    sf = _mock_sf(scalars_return=[])
    clock = SimulatedClock()
    clock.advance(datetime(2025, 1, 6, 10, 0, tzinfo=UTC))
    mocks = _all_mocked()
    with patch("trading.reports.engine.fetch_signals", new=mocks["trading.reports.engine.fetch_signals"]), \
         patch("trading.reports.engine.fetch_decisions", new=mocks["trading.reports.engine.fetch_decisions"]), \
         patch("trading.reports.engine.fetch_audit_logs", new=mocks["trading.reports.engine.fetch_audit_logs"]), \
         patch("trading.reports.engine.fetch_heartbeats", new=mocks["trading.reports.engine.fetch_heartbeats"]), \
         patch("trading.reports.engine.fetch_algo_configs", new=mocks["trading.reports.engine.fetch_algo_configs"]), \
         patch("trading.reports.engine.fetch_nifty_benchmark", new=mocks["trading.reports.engine.fetch_nifty_benchmark"]):
        app = build_app(sf, clock=clock)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/reports/live?period=day")

    assert resp.status_code == 200
    data = resp.json()
    assert "signal_funnel" in data
    assert "order_funnel" in data
    assert "pnl_summary" in data


@pytest.mark.asyncio
async def test_get_live_report_week_period():
    sf = _mock_sf(scalars_return=[])
    clock = SimulatedClock()
    clock.advance(datetime(2025, 1, 6, 10, 0, tzinfo=UTC))
    mocks = _all_mocked()
    with patch("trading.reports.engine.fetch_signals", new=mocks["trading.reports.engine.fetch_signals"]), \
         patch("trading.reports.engine.fetch_decisions", new=mocks["trading.reports.engine.fetch_decisions"]), \
         patch("trading.reports.engine.fetch_audit_logs", new=mocks["trading.reports.engine.fetch_audit_logs"]), \
         patch("trading.reports.engine.fetch_heartbeats", new=mocks["trading.reports.engine.fetch_heartbeats"]), \
         patch("trading.reports.engine.fetch_algo_configs", new=mocks["trading.reports.engine.fetch_algo_configs"]), \
         patch("trading.reports.engine.fetch_nifty_benchmark", new=mocks["trading.reports.engine.fetch_nifty_benchmark"]):
        app = build_app(sf, clock=clock)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/reports/live?period=week")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_live_report_month_period():
    sf = _mock_sf(scalars_return=[])
    clock = SimulatedClock()
    clock.advance(datetime(2025, 1, 6, 10, 0, tzinfo=UTC))
    mocks = _all_mocked()
    with patch("trading.reports.engine.fetch_signals", new=mocks["trading.reports.engine.fetch_signals"]), \
         patch("trading.reports.engine.fetch_decisions", new=mocks["trading.reports.engine.fetch_decisions"]), \
         patch("trading.reports.engine.fetch_audit_logs", new=mocks["trading.reports.engine.fetch_audit_logs"]), \
         patch("trading.reports.engine.fetch_heartbeats", new=mocks["trading.reports.engine.fetch_heartbeats"]), \
         patch("trading.reports.engine.fetch_algo_configs", new=mocks["trading.reports.engine.fetch_algo_configs"]), \
         patch("trading.reports.engine.fetch_nifty_benchmark", new=mocks["trading.reports.engine.fetch_nifty_benchmark"]):
        app = build_app(sf, clock=clock)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/reports/live?period=month")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_live_report_with_explicit_date():
    sf = _mock_sf(scalars_return=[])
    mocks = _all_mocked()
    with patch("trading.reports.engine.fetch_signals", new=mocks["trading.reports.engine.fetch_signals"]), \
         patch("trading.reports.engine.fetch_decisions", new=mocks["trading.reports.engine.fetch_decisions"]), \
         patch("trading.reports.engine.fetch_audit_logs", new=mocks["trading.reports.engine.fetch_audit_logs"]), \
         patch("trading.reports.engine.fetch_heartbeats", new=mocks["trading.reports.engine.fetch_heartbeats"]), \
         patch("trading.reports.engine.fetch_algo_configs", new=mocks["trading.reports.engine.fetch_algo_configs"]), \
         patch("trading.reports.engine.fetch_nifty_benchmark", new=mocks["trading.reports.engine.fetch_nifty_benchmark"]):
        app = build_app(sf)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/reports/live?period=day&date=2025-01-06")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_live_report_unknown_period_returns_400():
    sf = _mock_sf(scalars_return=[])
    app = build_app(sf)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/reports/live?period=quarter")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/auth/login-url — auth endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_login_url_without_api_key_returns_503():
    sf = _mock_sf(scalars_return=[])
    app = build_app(sf)  # no zerodha_api_key
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/auth/login-url")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_auth_callback_without_credentials_returns_503():
    sf = _mock_sf(scalars_return=[])
    app = build_app(sf)  # no credentials
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/callback", json={"request_token": "tok"})
    assert resp.status_code == 503
