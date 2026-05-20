"""
Clock abstraction for live and simulated time.

Live trading uses ``SystemClock`` which delegates to ``datetime.now(UTC)``.
Backtesting uses ``SimulatedClock`` whose current time is advanced by the
CandlePlayer at each bar, so all components that call ``clock.now()`` see
the bar's timestamp rather than the wall clock.

The clock also vends timezone-aware helpers so callers never hardcode
``ZoneInfo("Asia/Kolkata")`` or manual ``timedelta(hours=5, minutes=30)``
offsets. The timezone is read from settings (``TIMEZONE`` env / .env) and
defaults to ``"Asia/Kolkata"``.

Usage
-----
Inject the clock wherever ``datetime.now(UTC)`` was previously called::

    from trading.core.clock import Clock, SystemClock

    class MyComponent:
        def __init__(self, clock: Clock = SYSTEM_CLOCK):
            self._clock = clock

        def do_something(self):
            now      = self._clock.now()       # UTC datetime
            now_tz   = self._clock.now_tz()    # same instant in configured tz
            today    = self._clock.today()     # date in configured tz
            session  = self._clock.session_open_utc(time(9, 15))  # today's open as UTC

The singleton ``SYSTEM_CLOCK`` is available for convenience in production code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo


class Clock(ABC):
    """Abstract time provider."""

    @property
    @abstractmethod
    def tz(self) -> ZoneInfo:
        """The configured local timezone (e.g. Asia/Kolkata)."""
        ...

    @abstractmethod
    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...

    def now_tz(self) -> datetime:
        """Return the current time expressed in the configured timezone."""
        n = self.now()
        if n == datetime.min.replace(tzinfo=UTC):
            # SimulatedClock before first advance() — return as-is to avoid overflow
            return n
        return n.astimezone(self.tz)

    def today(self) -> date:
        """Return today's calendar date in the configured timezone."""
        return self.now_tz().date()

    def session_open_utc(self, session_open: time = time(9, 15)) -> datetime:
        """
        Return today's session open as a UTC datetime.

        *session_open* is a wall-clock time in the configured timezone
        (default 09:15 for NSE). The returned datetime is timezone-aware UTC.
        """
        local_today = self.today()
        open_local = datetime(
            local_today.year,
            local_today.month,
            local_today.day,
            session_open.hour,
            session_open.minute,
            tzinfo=self.tz,
        )
        return open_local.astimezone(UTC)


class SystemClock(Clock):
    """
    Production clock — delegates to the OS.

    The timezone is read lazily from settings on first access so that
    ``SYSTEM_CLOCK`` (constructed at import time) works without requiring
    settings to be loaded at module import.
    """

    def __init__(self, timezone: str | None = None) -> None:
        self._timezone = timezone
        self._tz: ZoneInfo | None = None

    @property
    def tz(self) -> ZoneInfo:
        if self._tz is None:
            if self._timezone:
                self._tz = ZoneInfo(self._timezone)
            else:
                from trading.config.settings import get_settings

                self._tz = ZoneInfo(get_settings().timezone)
        return self._tz

    def now(self) -> datetime:
        return datetime.now(UTC)


class SimulatedClock(Clock):
    """
    Simulated clock for backtesting.

    The current time is set externally (by CandlePlayer at each bar) via
    ``advance(ts)``. All components that use this clock will see the candle
    bar's timestamp instead of the wall clock.

    Starts at ``datetime.min`` (UTC) so any time check against it before
    the first ``advance()`` call is safely in the past.
    """

    def __init__(self, timezone: str = "Asia/Kolkata") -> None:
        self._tz = ZoneInfo(timezone)
        self._current: datetime = datetime.min.replace(tzinfo=UTC)

    @property
    def tz(self) -> ZoneInfo:
        return self._tz

    def advance(self, ts: datetime) -> None:
        """Advance the clock to *ts*. Called by CandlePlayer at each bar."""
        self._current = ts

    def now(self) -> datetime:
        return self._current


# Singleton for convenience — production code can import this directly.
# Timezone is resolved lazily from settings on first use.
SYSTEM_CLOCK: Clock = SystemClock()
