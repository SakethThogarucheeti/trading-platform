from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading.core.clock import Clock
from trading.storage.cache import CacherFactory

from ._helpers import cached_json_response

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
            local_date = datetime.fromisoformat(date).replace(tzinfo=clock.tz)
        else:
            now_tz = clock.now_tz()
            if now_tz == datetime.min.replace(tzinfo=UTC):
                local_date = now_tz  # SimulatedClock before first advance() — avoid tz overflow
            else:
                local_date = datetime(now_tz.year, now_tz.month, now_tz.day, tzinfo=clock.tz)

        # All period math happens in the local (IST) calendar, converting to
        # UTC only once at the end. Deriving day/week/month boundaries from a
        # UTC-*converted* midnight is wrong: 00:00 IST is 18:30 UTC the
        # previous day, so day/month/weekday read off that shifted value can
        # land on the wrong calendar day — for "day" specifically, that used
        # to make `end` only ~5.5 hours after `start`, ending the window at
        # 05:29:59 IST, hours before the market even opens at 09:15 IST, so
        # the entire trading session fell outside every daily report.
        if period == "day":
            local_start = local_date
            local_end = local_date.replace(hour=23, minute=59, second=59)
        elif period == "week":
            local_start = local_date - timedelta(days=local_date.weekday())
            local_end = local_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        elif period == "month":
            import calendar
            local_start = local_date.replace(day=1)
            last_day = calendar.monthrange(local_date.year, local_date.month)[1]
            local_end = local_date.replace(day=last_day, hour=23, minute=59, second=59)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown period: {period!r}")

        start = local_start.astimezone(UTC)
        end = local_end.astimezone(UTC)

        async def _produce() -> str:
            from trading.reports.engine import fetch_report_data
            data = await fetch_report_data(start, end, session_factory)
            return data.model_dump_json()

        return await cached_json_response(
            cacher_factory,
            key_args=("report", period, local_date.date().isoformat()),
            producer=_produce,
            ttl=60,
        )

    @router.get("/api/reports/{session_id}")
    async def get_report(session_id: str) -> JSONResponse:
        report_file = results_dir / session_id / "report.json"
        if not report_file.exists():
            raise HTTPException(status_code=404, detail=f"Report not found: {session_id}")
        data = json.loads(report_file.read_text(encoding="utf-8"))
        return JSONResponse(content=data)

    return router
