from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]

from trading.config.settings import Settings

logger = logging.getLogger(__name__)


class Scheduler:
    """
    In-process cron scheduler wrapping APScheduler's AsyncIOScheduler.

    Market hours (Mon–Fri IST):
    - 09:15  Start Runtime (market open)
    - 15:30  Stop Runtime (market close)
    - 15:45  Run EOD report (called once per trading day)

    Weekly:
    - Sunday 10:00  Sync instruments from broker to DB

    Usage
    -----
    ::

        scheduler = Scheduler(settings, runtime_start=..., runtime_stop=...,
                              eod_report=..., sync_instruments=...)
        scheduler.start()
        # blocks via main loop; scheduler fires jobs as cron
        scheduler.stop()
    """

    def __init__(
        self,
        settings: Settings,
        on_market_open: Any | None = None,  # async callable
        on_market_close: Any | None = None,  # async callable
        on_eod: Any | None = None,  # async callable
        on_sync: Any | None = None,  # async callable
        on_position_reset: Any | None = None,  # async callable — paper EOD square-off
    ) -> None:
        self._settings = settings
        self._on_market_open = on_market_open
        self._on_market_close = on_market_close
        self._on_eod = on_eod
        self._on_sync = on_sync
        self._on_position_reset = on_position_reset
        self._scheduler: AsyncIOScheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
        self._register_jobs()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._scheduler.start()
        logger.info("Scheduler: started")

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler: stopped")

    # ------------------------------------------------------------------
    # Job registration
    # ------------------------------------------------------------------

    def _register_jobs(self) -> None:
        if self._on_market_open:
            self._scheduler.add_job(  # type: ignore[reportUnknownMemberType]
                self._on_market_open,
                trigger="cron",
                day_of_week="mon-fri",
                hour=9,
                minute=15,
                id="market_open",
                name="Market Open — start runtime",
            )

        if self._on_market_close:
            self._scheduler.add_job(  # type: ignore[reportUnknownMemberType]
                self._on_market_close,
                trigger="cron",
                day_of_week="mon-fri",
                hour=15,
                minute=30,
                id="market_close",
                name="Market Close — stop runtime",
            )

        if self._on_eod:
            self._scheduler.add_job(  # type: ignore[reportUnknownMemberType]
                self._on_eod,
                trigger="cron",
                day_of_week="mon-fri",
                hour=15,
                minute=45,
                id="eod_report",
                name="EOD Report",
            )

        if self._on_sync:
            self._scheduler.add_job(  # type: ignore[reportUnknownMemberType]
                self._on_sync,
                trigger="cron",
                day_of_week="sun",
                hour=10,
                minute=0,
                id="instrument_sync",
                name="Weekly Instrument Sync",
            )

        if self._on_position_reset:
            self._scheduler.add_job(  # type: ignore[reportUnknownMemberType]
                self._on_position_reset,
                trigger="cron",
                day_of_week="mon-fri",
                hour=15,
                minute=29,
                id="position_reset",
                name="EOD Position Reset — square off paper positions",
            )

    def get_job_ids(self) -> list[str]:
        return [job.id for job in self._scheduler.get_jobs()]  # type: ignore[reportUnknownMemberType]