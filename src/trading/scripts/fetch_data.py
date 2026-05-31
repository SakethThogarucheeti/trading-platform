"""
Fetch historical OHLCV data from Zerodha and save as Parquet for backtesting.

Usage
-----
    # Fetch last 365 days of 1min + day data for symbols in the DB
    uv run python -m trading.scripts.fetch_data

    # Specific symbols, intervals, and date range
    uv run python -m trading.scripts.fetch_data \\
        --symbols INFY TCS RELIANCE \\
        --intervals 1min 5min day \\
        --days 180 \\
        --out data/

Output layout
-------------
    data/
    └── INFY/
        ├── 1min.parquet   (appended / updated on re-runs)
        ├── 5min.parquet
        └── day.parquet

Re-running the script is safe: existing Parquet files are read, the date
range is trimmed to only what is missing, and new rows are appended.
Zerodha API limits are respected automatically (60-day chunks for intraday,
400-day chunks for daily).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta

from trading.core.clock import SystemClock

_clock = SystemClock()
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Zerodha API per-call date range limits
_INTRADAY_CHUNK_DAYS = 60
_DAILY_CHUNK_DAYS = 400

# Throttle: Kite historical data API allows ~3 req/s; stay well under
_REQUEST_DELAY_SECS = 0.4

# Intervals treated as intraday (use 60-day chunks)
_INTRADAY_INTERVALS = {"1min", "3min", "5min", "10min", "15min", "30min", "60min"}


def _chunk_ranges(
    start: datetime, end: datetime, chunk_days: int
) -> list[tuple[datetime, datetime]]:
    """Split [start, end] into chunks of at most chunk_days days."""
    ranges: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        ranges.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(seconds=1)
    return ranges


def _existing_range(path: Path) -> tuple[datetime, datetime] | None:
    """Return (min_date, max_date) of an existing Parquet file, or None."""
    if not path.exists():
        return None
    df = pl.read_parquet(path, columns=["date"])
    if df.is_empty():
        return None
    dates = df["date"].cast(pl.Datetime("us", "UTC"))
    min_val = dates.min()
    max_val = dates.max()
    if not isinstance(min_val, datetime) or not isinstance(max_val, datetime):
        return None
    return min_val.replace(tzinfo=UTC), max_val.replace(tzinfo=UTC)


def _append_or_create(path: Path, new_df: pl.DataFrame) -> None:
    """Merge new_df into existing Parquet (dedup by date), or create it."""
    if path.exists():
        existing = pl.read_parquet(path)
        combined = pl.concat([existing, new_df]).unique(subset=["date"]).sort("date")
    else:
        combined = new_df.sort("date")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    combined.write_parquet(tmp)
    tmp.replace(path)  # replace() is atomic and works on Windows unlike rename()


def _fetch_symbol(
    broker: object,  # ZerodhaBroker
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    out_dir: Path,
) -> None:
    path = out_dir / symbol / f"{interval}.parquet"

    # Trim start to just after what we already have
    existing = _existing_range(path)
    if existing is not None:
        _, existing_end = existing
        if existing_end >= end:
            log.info(
                "  %s/%s — already up to date (have data through %s)",
                symbol,
                interval,
                existing_end.date(),
            )
            return
        fetch_start = existing_end + timedelta(seconds=1)
        log.info(
            "  %s/%s — fetching %s → %s (have up to %s)",
            symbol,
            interval,
            fetch_start.date(),
            end.date(),
            existing_end.date(),
        )
    else:
        fetch_start = start
        log.info(
            "  %s/%s — fetching %s → %s (no existing data)",
            symbol,
            interval,
            fetch_start.date(),
            end.date(),
        )

    chunk_days = _INTRADAY_CHUNK_DAYS if interval in _INTRADAY_INTERVALS else _DAILY_CHUNK_DAYS
    chunks = _chunk_ranges(fetch_start, end, chunk_days)
    all_frames: list[pl.DataFrame] = []

    for i, (c_start, c_end) in enumerate(chunks):
        log.info(
            "    chunk %d/%d: %s → %s",
            i + 1,
            len(chunks),
            c_start.date(),
            c_end.date(),
        )
        try:
            df: pl.DataFrame = broker.get_ohlc(symbol, interval, c_start, c_end)  # type: ignore[attr-defined]
            all_frames.append(df)  # type: ignore[arg-type]
        except ValueError as e:
            # Kite returns empty for market holidays / weekends — not an error
            log.warning("    skipped: %s", e)
        time.sleep(_REQUEST_DELAY_SECS)

    if not all_frames:
        log.warning("  %s/%s — no new data returned", symbol, interval)
        return

    _FLOAT_COLS = ["open", "high", "low", "close"]
    cast_frames = [
        f.with_columns([pl.col(c).cast(pl.Float64) for c in _FLOAT_COLS if c in f.columns])
        for f in all_frames
    ]
    new_data = pl.concat(cast_frames).unique(subset=["date"]).sort("date")
    _append_or_create(path, new_data)
    log.info("  %s/%s — saved %d rows to %s", symbol, interval, len(new_data), path)


def _symbols_from_db() -> list[str]:
    """Load symbol list from the trading DB (requires DB to be running)."""
    import anyio
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from trading.config.settings import get_settings
    from trading.core.models import Instrument

    settings = get_settings()

    async def _fetch() -> list[str]:
        engine = create_async_engine(str(settings.postgres_url))
        sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sf() as session:
            rows = (await session.execute(select(Instrument))).scalars().all()
        await engine.dispose()
        return [r.symbol for r in rows]

    return anyio.run(_fetch)


def _build_broker() -> object:
    from trading.broker.zerodha.broker import ZerodhaBroker
    from trading.broker.zerodha.kite_client import KiteClient

    api_key = os.environ.get("ZERODHA_API_KEY", "")
    access_token = os.environ.get("ZERODHA_ACCESS_TOKEN", "")

    if not api_key:
        sys.exit("ERROR: ZERODHA_API_KEY not set in .env")
    if not access_token:
        sys.exit(
            "ERROR: ZERODHA_ACCESS_TOKEN not set in .env\n"
            "Run: uv run python -m trading.scripts.login"
        )

    client = KiteClient(api_key)
    client.set_access_token(access_token)
    return ZerodhaBroker(client)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch historical OHLCV data from Zerodha")
    parser.add_argument(
        "--symbols",
        nargs="+",
        metavar="SYM",
        help="Symbols to fetch (default: all instruments in DB)",
    )
    parser.add_argument(
        "--intervals",
        nargs="+",
        default=["1min", "5min", "15min", "day"],
        metavar="INTERVAL",
        help="Intervals to fetch (default: 1min 5min 15min day)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="How many calendar days of history to fetch (default: 365)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data"),
        metavar="DIR",
        help="Output directory (default: data/)",
    )
    args = parser.parse_args()

    today = _clock.today()
    end = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=_clock.tz).astimezone(UTC)
    start_date = _clock.today() - timedelta(days=args.days)
    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=_clock.tz).astimezone(UTC)

    broker = _build_broker()

    if args.symbols:
        symbols = args.symbols
    else:
        log.info("No symbols specified — loading from DB …")
        symbols = _symbols_from_db()
        if not symbols:
            sys.exit("ERROR: no instruments found in DB. Add instruments first or pass --symbols.")

    log.info(
        "Fetching %d symbol(s) × %d interval(s) from %s to %s → %s",
        len(symbols),
        len(args.intervals),
        start.date(),
        end.date(),
        args.out,
    )

    for symbol in symbols:
        for interval in args.intervals:
            _fetch_symbol(broker, symbol, interval, start, end, args.out)

    log.info("Done.")


if __name__ == "__main__":
    main()
