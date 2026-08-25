from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.sql.elements import ColumnElement

from trading.core.clock import Clock
from trading.core.models import DecisionLog


def session_filter(model: type[DecisionLog], session_id: str) -> ColumnElement[bool]:
    if session_id:
        return model.session_id == session_id
    return model.session_id.is_(None)


def today_start(clock: Clock) -> datetime:
    """Start of the current local-timezone day, as a UTC datetime."""
    now_tz = clock.now_tz()
    if now_tz == datetime.min.replace(tzinfo=UTC):
        return now_tz  # SimulatedClock before first advance() — avoid tz-conversion overflow
    return datetime(now_tz.year, now_tz.month, now_tz.day, tzinfo=clock.tz).astimezone(UTC)


def parse_utc_datetime(value: str) -> datetime:
    """Parse an ISO datetime string, assuming UTC when it carries no tzinfo.

    Raises ValueError on an unparseable string — callers wrap this into an
    HTTPException with their own message/status code.
    """
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
