"""Tests for core/clock.py"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading.core.clock import SYSTEM_CLOCK, Clock, SimulatedClock, SystemClock


def test_system_clock_returns_utc_datetime() -> None:
    sc = SystemClock()
    now = sc.now()
    assert now.tzinfo is not None
    assert now.tzinfo == UTC or str(now.tzinfo) == "UTC"


def test_system_clock_is_recent() -> None:
    sc = SystemClock()
    before = datetime.now(UTC)
    result = sc.now()
    after = datetime.now(UTC)
    assert before <= result <= after


def test_clock_is_abstract() -> None:
    with pytest.raises(TypeError):
        Clock()  # type: ignore[abstract]


def test_simulated_clock_starts_at_min() -> None:
    sc = SimulatedClock()
    assert sc.now() == datetime.min.replace(tzinfo=UTC)


def test_simulated_clock_advance() -> None:
    sc = SimulatedClock()
    ts = datetime(2025, 6, 1, 9, 15, tzinfo=UTC)
    sc.advance(ts)
    assert sc.now() == ts


def test_simulated_clock_advance_multiple_times() -> None:
    sc = SimulatedClock()
    t1 = datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
    t2 = datetime(2025, 1, 1, 9, 5, tzinfo=UTC)
    sc.advance(t1)
    assert sc.now() == t1
    sc.advance(t2)
    assert sc.now() == t2


def test_system_clock_singleton_is_clock_instance() -> None:
    assert isinstance(SYSTEM_CLOCK, Clock)
    assert isinstance(SYSTEM_CLOCK, SystemClock)


def test_simulated_clock_now_tz_before_advance_does_not_crash() -> None:
    """Covers line 59: SimulatedClock.now_tz() before advance returns datetime.min equivalent."""
    sc = SimulatedClock()
    result = sc.now_tz()
    # The clock returns datetime.min (UTC) before any advance() call
    assert result == datetime.min.replace(tzinfo=UTC)


def test_system_clock_tz_loads_from_settings_when_timezone_is_none() -> None:
    """Covers the else branch (line 104-106): SystemClock.tz lazy-loads from settings when _timezone is None."""
    from zoneinfo import ZoneInfo

    clock = SystemClock(timezone=None)
    tz = clock.tz
    assert isinstance(tz, ZoneInfo)


def test_system_clock_tz_uses_provided_timezone_string() -> None:
    """Covers line 102: SystemClock.tz uses ZoneInfo(_timezone) when _timezone is set."""
    from zoneinfo import ZoneInfo

    clock = SystemClock(timezone="Asia/Kolkata")
    tz = clock.tz
    assert isinstance(tz, ZoneInfo)
    assert str(tz) == "Asia/Kolkata"
