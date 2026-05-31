from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.clock import Clock
from trading.storage.cache import CacherFactory

logger = logging.getLogger(__name__)


def create_reports_router(
    results_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    cacher_factory: CacherFactory | None,
) -> APIRouter:
    router = APIRouter()

    # /api/reports/sessions must be registered before /api/reports/live,
    # and /api/reports/live before /api/reports/{session_id}, so that the
    # literal path segments are not swallowed by the wildcard.

    @router.get("/api/reports/sessions")
    async def get_report_sessions() -> JSONResponse:
        sessions: list[dict[str, object]] = []
        if not results_dir.exists():
            return JSONResponse(content=sessions)
        for report_file in sorted(results_dir.glob("*/report.json")):
            try:
                data = json.loads(report_file.read_text(encoding="utf-8"))
                sessions.append(
                    {
                        "session_id": data.get("session_id", ""),
                        "session_type": data.get("session_type", ""),
                        "algo_name": data.get("algo_name", ""),
                        "started_at": data.get("started_at", ""),
                        "finished_at": data.get("finished_at", ""),
                    }
                )
            except Exception:
                logger.debug("Skipping malformed report: %s", report_file)
        return JSONResponse(content=sessions)

    @router.get("/api/reports/live")
    async def get_live_report(
        period: str = "day",
        date: str = "",
    ) -> JSONResponse:
        if date:
            target_date = datetime.fromisoformat(date).replace(tzinfo=UTC)
        else:
            today = clock.today()
            target_date = datetime(today.year, today.month, today.day, tzinfo=clock.tz).astimezone(UTC)

        if period == "day":
            start = target_date
            end = target_date.replace(hour=23, minute=59, second=59)
        elif period == "week":
            import datetime as _dt
            start = target_date - _dt.timedelta(days=target_date.weekday())
            end = start + _dt.timedelta(days=6, hours=23, minutes=59, seconds=59)
        elif period == "month":
            import calendar
            start = target_date.replace(day=1)
            last_day = calendar.monthrange(target_date.year, target_date.month)[1]
            end = target_date.replace(day=last_day, hour=23, minute=59, second=59)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown period: {period!r}")

        async def _produce() -> str:
            from trading.reports.engine import fetch_report_data
            data = await fetch_report_data(start, end, session_factory)
            return data.model_dump_json()

        if cacher_factory is not None:
            body = await cacher_factory.api().get_or_set_response(  # type: ignore[reportUnknownMemberType]
                key_args=("report", period, target_date.date().isoformat()),
                producer=_produce,
                ttl=60,
            )
            return JSONResponse(content=json.loads(body))
        return JSONResponse(content=json.loads(await _produce()))

    @router.get("/api/reports/{session_id}")
    async def get_report(session_id: str) -> JSONResponse:
        report_file = results_dir / session_id / "report.json"
        if not report_file.exists():
            raise HTTPException(status_code=404, detail=f"Report not found: {session_id}")
        data = json.loads(report_file.read_text(encoding="utf-8"))
        return JSONResponse(content=data)

    return router
