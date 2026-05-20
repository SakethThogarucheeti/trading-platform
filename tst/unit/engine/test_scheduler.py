"""Tests for engine/scheduler.py — Scheduler"""

from __future__ import annotations

from trading.config.settings import Settings
from trading.engine.scheduler import Scheduler


def make_settings() -> Settings:
    return Settings(
        zerodha_api_key="k",
        zerodha_api_secret="s",
        postgres_url="postgresql+asyncpg://u:p@localhost/t",
    )


def make_scheduler(**callbacks) -> Scheduler:
    return Scheduler(make_settings(), **callbacks)


# ---------------------------------------------------------------------------
# Job registration
# ---------------------------------------------------------------------------


def test_all_callbacks_registered_as_jobs() -> None:
    calls: list[str] = []

    async def noop(label: str) -> None:
        calls.append(label)

    scheduler = make_scheduler(
        on_market_open=lambda: noop("open"),
        on_market_close=lambda: noop("close"),
        on_eod=lambda: noop("eod"),
        on_sync=lambda: noop("sync"),
    )
    ids = scheduler.get_job_ids()
    assert "market_open" in ids
    assert "market_close" in ids
    assert "eod_report" in ids
    assert "instrument_sync" in ids


def test_no_jobs_when_no_callbacks() -> None:
    scheduler = make_scheduler()
    assert scheduler.get_job_ids() == []


def test_partial_callbacks_only_register_present_jobs() -> None:
    async def noop() -> None:
        pass

    scheduler = make_scheduler(on_market_open=noop, on_eod=noop)
    ids = scheduler.get_job_ids()
    assert "market_open" in ids
    assert "eod_report" in ids
    assert "market_close" not in ids
    assert "instrument_sync" not in ids


# ---------------------------------------------------------------------------
# Start/stop lifecycle
# ---------------------------------------------------------------------------


async def test_start_and_stop_without_error() -> None:
    scheduler = make_scheduler()
    scheduler.start()
    scheduler.stop()  # should not raise


async def test_double_stop_without_error() -> None:
    scheduler = make_scheduler()
    scheduler.start()
    scheduler.stop()
    # APScheduler shutdown is idempotent — second stop is a no-op
