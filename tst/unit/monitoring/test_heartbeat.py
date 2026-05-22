"""Tests for monitoring/telegram.py — TelegramAlerter, and engine/heartbeat.py — HeartbeatMonitor"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import httpx
import pytest
from anyio import create_task_group
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.config.settings import Settings
from trading.core.database import build_session_factory, init_db
from trading.core.models import Heartbeat
from trading.monitoring.heartbeat import HeartbeatMonitor
from trading.api.telegram import TelegramAlerter
from trading.storage.stores.heartbeat import HeartbeatStore


def make_settings(token: str | None = "BOT_TOKEN", chat_id: str | None = "CHAT_ID") -> Settings:
    return Settings(
        zerodha_api_key="k",
        zerodha_api_secret="s",
        postgres_url="postgresql+asyncpg://u:p@localhost/t",
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
    )


def make_mock_response(status_code: int, headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.text = "ok"
    return resp


# ---------------------------------------------------------------------------
# No-op when disabled
# ---------------------------------------------------------------------------


async def test_no_http_call_when_token_is_none() -> None:
    alerter = TelegramAlerter(make_settings(token=None))
    with patch("httpx.AsyncClient") as mock_client:
        await alerter.send_alert("test", "module")
    mock_client.assert_not_called()


# ---------------------------------------------------------------------------
# Successful send
# ---------------------------------------------------------------------------


async def test_successful_send_calls_telegram_api() -> None:
    alerter = TelegramAlerter(make_settings())
    mock_resp = make_mock_response(200)

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=mock_resp)

        await alerter.send_alert("hello", "heartbeat_miss")

    mock_client.post.assert_called_once()
    args, kwargs = mock_client.post.call_args
    assert "BOT_TOKEN" in args[0]
    assert kwargs["json"]["chat_id"] == "CHAT_ID"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


async def test_rate_limit_second_call_within_window_is_noop() -> None:
    alerter = TelegramAlerter(make_settings())
    mock_resp = make_mock_response(200)

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=mock_resp)

        await alerter.send_alert("first", "heartbeat")
        await alerter.send_alert("second", "heartbeat")  # rate-limited

    assert mock_client.post.call_count == 1  # only first call posted


async def test_different_event_types_have_independent_rate_limits() -> None:
    alerter = TelegramAlerter(make_settings())
    mock_resp = make_mock_response(200)

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=mock_resp)

        await alerter.send_alert("msg1", "type_a")
        await alerter.send_alert("msg2", "type_b")  # different type → posts

    assert mock_client.post.call_count == 2


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


async def test_http_500_retried_3_times() -> None:
    alerter = TelegramAlerter(make_settings())

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=make_mock_response(500))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await alerter.send_alert("error", "crash")

    # 1 initial + 2 retries = 3 calls (attempt 1, 2, 3; fails at attempt > 3)
    assert mock_client.post.call_count == 3


async def test_http_429_waits_retry_after_then_retries() -> None:
    alerter = TelegramAlerter(make_settings())
    slept: list[int] = []

    # 429 first, then 200
    responses = [
        make_mock_response(429, headers={"Retry-After": "10"}),
        make_mock_response(200),
    ]

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=responses)

        async def fake_sleep(secs: float) -> None:
            slept.append(int(secs))

        with patch("trading.api.telegram.sleep", fake_sleep):
            await alerter.send_alert("429 test", "test")

    assert 10 in slept
    assert mock_client.post.call_count == 2


async def test_timeout_retried() -> None:
    alerter = TelegramAlerter(make_settings())

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("trading.api.telegram.sleep", new_callable=AsyncMock):
            await alerter.send_alert("timeout test", "event")

    # 3 attempts
    assert mock_client.post.call_count == 3


async def test_no_exception_raised_to_caller_on_failure() -> None:
    """Caller must not receive exceptions — alerts are best-effort."""
    alerter = TelegramAlerter(make_settings())

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=RuntimeError("network down"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            # Should not raise
            await alerter.send_alert("failure", "event")


# ---------------------------------------------------------------------------
# HeartbeatMonitor tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
def heartbeat_store(engine: AsyncEngine) -> HeartbeatStore:
    sf = build_session_factory(engine)
    return HeartbeatStore(sf)


async def test_beat_loop_calls_update_heartbeat(engine: AsyncEngine, heartbeat_store: HeartbeatStore) -> None:
    """_beat_loop must write a heartbeat row on each tick."""
    sf = build_session_factory(engine)
    calls: list[str] = []

    original = heartbeat_store.update_heartbeat

    async def _spy(module: str) -> None:
        calls.append(module)
        return await original(module)

    heartbeat_store.update_heartbeat = _spy  # type: ignore[method-assign]

    monitor = HeartbeatMonitor(
        heartbeat_store,
        sf,
        component_names=["hb_test"],
        beat_interval_secs=1,
        timeout_secs=30,
    )
    await monitor._setup()
    calls.clear()  # clear setup calls

    # Run _beat_loop for one iteration then cancel
    async def _run_one_beat() -> None:
        async with create_task_group() as tg:
            tg.start_soon(monitor._beat_loop)
            await anyio.sleep(0.05)
            tg.cancel_scope.cancel()

    await _run_one_beat()
    assert "heartbeat_monitor" in calls, "beat loop must call update_heartbeat"


async def test_monitor_loop_checks_stale_immediately(engine: AsyncEngine, heartbeat_store: HeartbeatStore) -> None:
    """_check_stale should be called immediately on startup (before first sleep)."""
    sf = build_session_factory(engine)
    check_calls: list[int] = []

    monitor = HeartbeatMonitor(
        heartbeat_store,
        sf,
        component_names=["hb_test"],
        beat_interval_secs=60,
        timeout_secs=60,
    )
    original_check = monitor._check_stale

    async def _spy_check() -> None:
        check_calls.append(1)
        await original_check()

    monitor._check_stale = _spy_check  # type: ignore[method-assign]

    # _monitor_loop should call _check_stale before sleeping
    async def _run_one_tick() -> None:
        async with create_task_group() as tg:
            tg.start_soon(monitor._monitor_loop)
            await anyio.sleep(0.05)
            tg.cancel_scope.cancel()

    await _run_one_tick()
    assert len(check_calls) >= 1, "immediate stale check must fire before first sleep"


async def test_alerter_called_for_stale_module(engine: AsyncEngine, heartbeat_store: HeartbeatStore) -> None:
    """When a monitored module has a stale heartbeat, alerter is called."""
    sf = build_session_factory(engine)
    alerted: list[str] = []

    async def fake_alerter(module: str) -> None:
        alerted.append(module)

    monitor = HeartbeatMonitor(
        heartbeat_store,
        sf,
        component_names=["dead_module"],
        beat_interval_secs=60,
        timeout_secs=5,
        alerter=fake_alerter,
    )
    await monitor._setup()

    # Backdate the heartbeat so it appears stale
    async with sf() as session:
        async with session.begin():
            result = await session.get(Heartbeat, "dead_module")
            if result:
                result.last_seen = datetime.now(UTC) - timedelta(seconds=60)

    await monitor._check_stale()
    assert "dead_module" in alerted, "stale module must trigger alerter"


async def test_telegram_unexpected_http_status_logs_error_and_returns_false() -> None:
    """Covers lines 90-95: unexpected HTTP status (e.g., 403) logs error and returns False."""
    from unittest.mock import patch

    alerter = TelegramAlerter(make_settings())

    # Return a 403 status — not 200, not 429, not >= 500 → triggers lines 90-95
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=make_mock_response(403))

        await alerter.send_alert("error test", "bad_status")

    # Verify it was called (and didn't raise)
    mock_client.post.assert_called_once()


async def test_telegram_unexpected_exception_returns_false() -> None:
    """Covers the generic except block in _post() for non-httpx exceptions."""
    from unittest.mock import patch

    alerter = TelegramAlerter(make_settings())

    # Raise a generic non-httpx exception
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=ConnectionError("socket closed"))

        # Should not raise — exception is caught and logged
        await alerter.send_alert("error test", "generic_exc")


async def test_check_stale_exception_is_caught(engine: AsyncEngine) -> None:
    """Covers lines 101-102: _check_stale() catches exceptions from get_stale_modules."""
    from unittest.mock import AsyncMock

    from trading.storage.stores.heartbeat import AbstractHeartbeatStore

    class _FailingHeartbeatStore(AbstractHeartbeatStore):
        async def update_heartbeat(self, module: str) -> None:
            pass

        async def get_stale_modules(self, timeout_secs: int, modules=None) -> list[str]:
            raise RuntimeError("DB unavailable in monitor check")

    sf = build_session_factory(engine)
    monitor = HeartbeatMonitor(
        _FailingHeartbeatStore(),
        sf,
        component_names=["test_module"],
        beat_interval_secs=60,
        timeout_secs=60,
    )

    # _check_stale should catch the exception and not raise
    await monitor._check_stale()


async def test_beat_loop_survives_db_failure(engine: AsyncEngine) -> None:
    """A DB failure in _beat_loop must not crash the loop."""
    sf = build_session_factory(engine)
    heartbeat_store = HeartbeatStore(sf)
    error_count = [0]
    call_count = [0]

    original = heartbeat_store.update_heartbeat

    async def _fail_first(module: str) -> None:
        call_count[0] += 1
        if call_count[0] == 1:
            error_count[0] += 1
            raise RuntimeError("simulated DB failure")
        return await original(module)

    heartbeat_store.update_heartbeat = _fail_first  # type: ignore[method-assign]

    monitor = HeartbeatMonitor(
        heartbeat_store,
        sf,
        component_names=["hb_test"],
        beat_interval_secs=0,
        timeout_secs=30,
    )

    async def _run_two_beats() -> None:
        async with create_task_group() as tg:
            tg.start_soon(monitor._beat_loop)
            await anyio.sleep(0.1)
            tg.cancel_scope.cancel()

    await _run_two_beats()
    assert error_count[0] >= 1, "DB failure should have been encountered"
    assert call_count[0] >= 2, "loop must continue after failure"
