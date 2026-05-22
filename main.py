"""
Trading platform entry point.

Lifecycle
---------
1. Build the DI container (connects DB engine + Redis client).
2. Run Alembic migrations to bring the schema up to date.
3. Resolve the Runtime and Scheduler from the container.
4. Start the APScheduler (fires Runtime.start at 09:15 IST, Runtime.stop at 15:30 IST).
5. If we are already inside market hours on startup, fire Runtime.start immediately.
6. Sleep forever — the scheduler drives everything from here.

The process exits cleanly on SIGTERM / KeyboardInterrupt; the DI container
disposes of all async resources (engine, redis) on context-manager exit.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from datetime import date, time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from anyio import sleep_forever

from trading.di.container import build_container
from trading.core.lifecycle.runtime import AbstractRuntime
from trading.monitoring.scheduler import Scheduler
from trading.api.dashboard.component import DashboardServer

if TYPE_CHECKING:
    from trading.config.settings import Settings

_LOG_DIR = Path("logs")
_LOG_DIR.mkdir(exist_ok=True)

_fmt = logging.Formatter(
    "%(asctime)s %(levelname)-8s [%(thread_id)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class _ThreadIdFilter(logging.Filter):
    """Inject the current thread_id context var into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        from trading.core.context import thread_id

        record.thread_id = thread_id.get()  # type: ignore[attr-defined]
        return True


_thread_id_filter = _ThreadIdFilter()

# Stream handler — live output to terminal, force UTF-8 on Windows
_stream_handler = logging.StreamHandler(
    open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)
)
_stream_handler.setFormatter(_fmt)
_stream_handler.addFilter(_thread_id_filter)

# One log file per calendar day — named logs/trading.2026-05-15.log at startup.
_file_handler = RotatingFileHandler(
    _LOG_DIR / f"trading.{date.today()}.log",
    maxBytes=50 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(_fmt)
_file_handler.addFilter(_thread_id_filter)

logging.basicConfig(level=logging.INFO, handlers=[_stream_handler, _file_handler])
logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
_MARKET_OPEN = time(9, 15)
_MARKET_CLOSE = time(15, 30)


def _check_port_free(port: int) -> None:
    """Exit with a clear message if *port* is already in use."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) != 0:
            return  # port is free

    # Find the owning PID on Windows for a helpful error message
    pid_hint = ""
    kill_hint = ""
    if sys.platform == "win32":
        result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if f":{port} " in line and "LISTENING" in line:
                pid = line.split()[-1]
                pid_hint = f" (PID {pid})"
                kill_hint = f"\n  Kill it:  taskkill /PID {pid} /F"
                break
    else:
        result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
        pid = result.stdout.strip()
        if pid:
            pid_hint = f" (PID {pid})"
            kill_hint = f"\n  Kill it:  kill -9 {pid}"

    sys.exit(
        f"ERROR: port {port} is already in use{pid_hint}.{kill_hint}"
    )


async def _sync_instruments(settings: "Settings") -> None:
    """Upsert instruments for all symbols declared in ALGOS into the DB."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    from trading.broker.zerodha.kite_client import KiteClient
    from trading.core.models import Instrument
    from trading.storage.stores.instrument import InstrumentStore

    symbols: set[str] = set()
    for algo in settings.algos:
        symbols.update(algo.instruments)

    if not symbols:
        logger.info("No ALGOS instruments configured — skipping instrument sync.")
        return

    logger.info("Syncing instruments for symbols: %s", sorted(symbols))

    client = KiteClient(settings.zerodha_api_key)

    _TYPE_MAP = {"EQ": "EQUITY", "FUT": "FUTURES", "CE": "OPTIONS", "PE": "OPTIONS"}

    kite_rows = client.instruments("NSE")
    matched: list[Instrument] = [
        Instrument(
            token=r["instrument_token"],
            symbol=r["tradingsymbol"],
            exchange=r["exchange"],
            instrument_type=_TYPE_MAP.get(r["instrument_type"], r["instrument_type"]),
        )
        for r in kite_rows
        if r["tradingsymbol"] in symbols
    ]

    missing = symbols - {m.symbol for m in matched}
    if missing:
        logger.warning("Instruments not found on NSE: %s", sorted(missing))

    if not matched:
        return

    engine = create_async_engine(str(settings.postgres_url))
    sf: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    store = InstrumentStore(sf)
    await store.upsert_instruments(matched)
    await engine.dispose()

    logger.info("Instrument sync complete — upserted %d instruments.", len(matched))


def _run_migrations() -> None:
    """Apply pending Alembic migrations synchronously before starting async code."""
    logger.info("Running Alembic migrations…")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("Alembic migration failed:\n%s", result.stderr)
        raise RuntimeError("DB migration failed — aborting startup")
    logger.info("Migrations complete.")


def _is_market_hours() -> bool:
    """Return True if the current IST time is within market hours on a weekday."""
    from datetime import datetime

    now_ist = datetime.now(_IST)
    if now_ist.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    t = now_ist.time()
    return _MARKET_OPEN <= t < _MARKET_CLOSE


async def _main() -> None:
    from trading.config.settings import get_settings

    settings = get_settings()
    if settings.dashboard_enabled:
        _check_port_free(settings.dashboard_port)

    _run_migrations()
    await _sync_instruments(settings)

    async with build_container() as container:
        from trading.broker.zerodha.kite_client import KiteClient
        from trading.core.database import build_engine, build_session_factory
        from trading.storage.stores.trading import TradingStore

        _engine = build_engine(str(settings.postgres_url))
        _sf = build_session_factory(_engine)
        _trading = TradingStore(_sf)
        _token = await _trading.get_broker_token("zerodha", settings.token_secret_key)
        await _engine.dispose()

        kite_client: KiteClient = await container.get(KiteClient)
        if _token:
            kite_client.set_access_token(_token)
            logger.info("Loaded Zerodha token from DB")
        else:
            logger.warning("No Zerodha token in DB — complete login before trading starts")

        runtime: AbstractRuntime = await container.get(AbstractRuntime)
        scheduler: Scheduler = await container.get(Scheduler)
        dashboard: DashboardServer | None = await container.get(DashboardServer | None)

        scheduler.start()
        logger.info("Scheduler started.")

        dashboard_task: asyncio.Task[None] | None = None
        if dashboard is not None:
            logger.info(
                "Dashboard starting on http://%s:%d",
                settings.dashboard_host,
                settings.dashboard_port,
            )
            dashboard_task = asyncio.get_event_loop().create_task(dashboard.start())

        runtime_task: asyncio.Task[None] | None = None
        if _is_market_hours():
            logger.info("Market is currently open — starting runtime immediately.")
            runtime_task = asyncio.get_event_loop().create_task(runtime.start())
        else:
            logger.info("Outside market hours — waiting for next 09:15 IST trigger.")

        try:
            await sleep_forever()
        finally:
            scheduler.stop()
            logger.info("Scheduler stopped.")
            runtime.stop()
            if runtime_task is not None and not runtime_task.done():
                await runtime_task
            if dashboard is not None:
                await dashboard.stop()
            if dashboard_task is not None and not dashboard_task.done():
                await dashboard_task


async def _run_worker(algo_name: str) -> None:
    """
    Worker process: subscribe to Redis ticks and run one named algo.

    Does NOT run migrations or instrument sync — the ingestor owns those.
    """
    from trading.di.container import build_worker_container

    logger.info("Worker starting: algo=%r", algo_name)
    async with build_worker_container(algo_name) as container:
        runtime: AbstractRuntime = await container.get(AbstractRuntime)
        scheduler: Scheduler = await container.get(Scheduler)
        scheduler.start()
        logger.info("Worker scheduler started for algo=%r", algo_name)

        runtime_task: asyncio.Task[None] | None = None
        if _is_market_hours():
            logger.info("Market is open — starting worker runtime immediately for algo=%r", algo_name)
            runtime_task = asyncio.get_event_loop().create_task(runtime.start())
        else:
            logger.info("Outside market hours — worker waiting for 09:15 IST trigger")

        try:
            await sleep_forever()
        finally:
            scheduler.stop()
            runtime.stop()
            if runtime_task is not None and not runtime_task.done():
                await runtime_task
            logger.info("Worker stopped for algo=%r", algo_name)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="main.py")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("ingestor", help="Run the Zerodha ingestor (default)")
    worker_p = sub.add_parser("worker", help="Run a strategy worker for one algo")
    worker_p.add_argument("--algo", required=True, metavar="NAME")
    args = parser.parse_args()

    try:
        if args.command == "worker":
            asyncio.run(_run_worker(args.algo))
        else:
            asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down.")
