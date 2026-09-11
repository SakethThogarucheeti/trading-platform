"""Tests for routers/stream.py's decisions_stream SSE endpoint — mocked DB.

Covers the previously-untested `_event_generator` scaffolding: the initial
"connected" event, prompt termination on client disconnect (mocking
`Request.is_disconnected()` rather than relying on ASGITransport's own
connection-closing behavior, so the test is deterministic), and SSE payload
formatting for a row found on one poll iteration.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.api.app import build_app
from trading.core.clock import SimulatedClock


def _mock_sf(scalars_return=None):
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = scalars_return or []

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_sf = MagicMock(spec=async_sessionmaker)
    mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_sf


async def _client(sf, clock=None, **kwargs):
    clock = clock or SimulatedClock()
    app = build_app(sf, clock, **kwargs)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class _FakeRow:
    def __init__(
        self,
        id: int,
        tick_log_id: int,
        step: str,
        symbol: str,
        algo_name: str,
        created_at: datetime,
        context: str,
    ) -> None:
        self.id = id
        self.tick_log_id = tick_log_id
        self.step = step
        self.symbol = symbol
        self.algo_name = algo_name
        self.created_at = created_at
        self.context = context


# ---------------------------------------------------------------------------
# GET /api/decisions/stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decisions_stream_sends_connected_event_then_stops_on_disconnect():
    """Client disconnects immediately -- generator should stop right after
    the initial "connected" event, never reaching anyio.sleep(2)."""
    sf = _mock_sf(scalars_return=[])
    with patch(
        "starlette.requests.Request.is_disconnected", new=AsyncMock(return_value=True)
    ):
        async with await _client(sf) as client, client.stream(
            "GET", "/api/decisions/stream"
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
    assert body == b": connected\n\n"


@pytest.mark.asyncio
async def test_decisions_stream_yields_one_row_as_sse_data_event():
    """Disconnect check returns False once (letting one poll iteration run),
    then True (stopping the loop) -- avoids the 2s anyio.sleep by patching
    it out, and avoids depending on real timing to bound the test."""
    row = _FakeRow(
        id=7,
        tick_log_id=42,
        step="SIGNAL_GENERATED",
        symbol="RELIANCE",
        algo_name="default",
        created_at=datetime(2026, 9, 12, 4, 0, tzinfo=UTC),
        context='{"reason": "test"}',
    )
    sf = _mock_sf(scalars_return=[row])
    with (
        patch(
            "starlette.requests.Request.is_disconnected",
            new=AsyncMock(side_effect=[False, True]),
        ),
        patch("anyio.sleep", new=AsyncMock()),
    ):
        async with await _client(sf) as client, client.stream(
            "GET", "/api/decisions/stream"
        ) as resp:
            assert resp.status_code == 200
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk

    text = body.decode()
    assert text.startswith(": connected\n\n")
    data_line = next(line for line in text.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload == {
        "id": 7,
        "tick_log_id": 42,
        "step": "SIGNAL_GENERATED",
        "symbol": "RELIANCE",
        "algo": "default",
        "ts": "2026-09-12T04:00:00+00:00",
        "context": {"reason": "test"},
    }


@pytest.mark.asyncio
async def test_decisions_stream_session_and_algo_filters_reach_the_query():
    """Query-string filters are forwarded into decision_log_base_conditions
    -- not asserting the SQL itself (test_helpers.py already covers that),
    just that the endpoint passes them through rather than dropping them."""
    sf = _mock_sf(scalars_return=[])
    with (
        patch(
            "starlette.requests.Request.is_disconnected",
            new=AsyncMock(side_effect=[False, True]),
        ),
        patch("anyio.sleep", new=AsyncMock()),
    ):
        async with await _client(sf) as client, client.stream(
            "GET", "/api/decisions/stream?session_id=abc&algo_name=rsi"
        ) as resp:
            assert resp.status_code == 200
            async for _ in resp.aiter_bytes():
                pass

    # One query executed (the single poll iteration before disconnecting).
    session = sf.return_value.__aenter__.return_value
    assert session.execute.await_count == 1
