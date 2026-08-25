from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi.responses import JSONResponse
from sqlalchemy.sql.elements import ColumnElement

from trading.core.clock import Clock
from trading.core.models import DecisionLog
from trading.storage.cache import CacherFactory


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


async def cached_json_response(
    cacher_factory: CacherFactory | None,
    key_args: tuple[object, ...],
    producer: Callable[[], Awaitable[str]],
    ttl: int,
) -> JSONResponse:
    """Serve a JSON route body from the API response cache when configured.

    `producer` returns the already-serialized JSON body. With a cacher_factory,
    the body is cached under `key_args` for `ttl` seconds; without one (e.g. in
    tests that don't wire caching), `producer` runs on every call.
    """
    if cacher_factory is not None:
        body = await cacher_factory.api().get_or_set_response(  # type: ignore[reportUnknownMemberType]
            key_args=key_args,
            producer=producer,
            ttl=ttl,
        )
        return JSONResponse(content=json.loads(body))
    return JSONResponse(content=json.loads(await producer()))


def parse_utc_datetime(value: str) -> datetime:
    """Parse an ISO datetime string, assuming UTC when it carries no tzinfo.

    Raises ValueError on an unparseable string — callers wrap this into an
    HTTPException with their own message/status code.
    """
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
