"""
Strategy worker entry point.

Runs the candle → signal → risk → execution pipeline for a single named algo,
receiving ticks from the ingestor process via Redis pub/sub.

Usage
-----
    python worker.py --algo ema_crossover

The algo name must match an entry in ALGOS (settings.algos[*].name).
The ingestor process (main.py) must be running first.

Lifecycle
---------
1. Parse --algo argument.
2. Build the worker DI container (no migrations, no instrument sync).
3. Start APScheduler (fires Runtime.start at 09:15 IST, Runtime.stop at 15:30 IST).
4. If already in market hours, start the Runtime immediately.
5. Sleep forever — the scheduler drives everything from here.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from anyio import sleep_forever

from trading.di.container import build_worker_container
from trading.engine.runtime import AbstractRuntime
from trading.engine.scheduler import Scheduler

_LOG_DIR = Path("logs")
_LOG_DIR.mkdir(exist_ok=True)

_fmt = logging.Formatter(
    "%(asctime)s %(levelname)-8s [%(thread_id)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class _ThreadIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        from trading.core.context import thread_id

        record.thread_id = thread_id.get()  # type: ignore[attr-defined]
        return True


_thread_id_filter = _ThreadIdFilter()

_stream_handler = logging.StreamHandler(
    open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)
)
_stream_handler.setFormatter(_fmt)
_stream_handler.addFilter(_thread_id_filter)

logging.basicConfig(level=logging.INFO, handlers=[_stream_handler])
logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
_MARKET_OPEN = time(9, 15)
_MARKET_CLOSE = time(15, 30)


def _parse_algo_arg() -> str:
    try:
        idx = sys.argv.index("--algo")
        return sys.argv[idx + 1]
    except (ValueError, IndexError):
        sys.exit("Usage: python worker.py --algo <algo_name>")


def _is_market_hours() -> bool:
    from datetime import datetime

    now_ist = datetime.now(_IST)
    if now_ist.weekday() >= 5:
        return False
    t = now_ist.time()
    return _MARKET_OPEN <= t < _MARKET_CLOSE


async def _main() -> None:
    algo_name = _parse_algo_arg()

    # Per-algo log file
    file_handler = RotatingFileHandler(
        _LOG_DIR / f"worker.{algo_name}.{date.today()}.log",
        maxBytes=50 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(_fmt)
    file_handler.addFilter(_thread_id_filter)
    logging.getLogger().addHandler(file_handler)

    logger.info("Worker starting for algo=%r", algo_name)

    async with build_worker_container(algo_name) as container:
        runtime: AbstractRuntime = await container.get(AbstractRuntime)
        scheduler: Scheduler = await container.get(Scheduler)

        scheduler.start()
        logger.info("Worker scheduler started.")

        runtime_task: asyncio.Task[None] | None = None
        if _is_market_hours():
            logger.info("Market is currently open — starting worker runtime immediately.")
            runtime_task = asyncio.get_event_loop().create_task(runtime.start())
        else:
            logger.info("Outside market hours — waiting for next 09:15 IST trigger.")

        try:
            await sleep_forever()
        finally:
            scheduler.stop()
            logger.info("Worker scheduler stopped.")
            runtime.stop()
            if runtime_task is not None and not runtime_task.done():
                await runtime_task


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Worker interrupted — shutting down.")
