"""
Import historical OHLCV candles from Parquet files into the Postgres candles table.

Reads the same data/ directory structure written by fetch-data and bulk-inserts
rows into the candles table. Re-running is safe: conflicts on (symbol, interval, ts)
are silently ignored so only missing rows are inserted.

Usage
-----
    uv run import-candles
    uv run import-candles --data-dir data/ --symbols INFY TCS --intervals 15min day
    uv run import-candles --data-dir data/ --batch-size 5000
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Generator
from datetime import UTC
from pathlib import Path

import anyio
import polars as pl
from quantindicators.types import CandleRow

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import Parquet candles into Postgres")
    p.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Root directory of the Parquet store (default: data/)",
    )
    p.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Symbols to import (default: all subdirectories found in data-dir)",
    )
    p.add_argument(
        "--intervals",
        nargs="*",
        default=None,
        help="Intervals to import e.g. 1min 15min day (default: all Parquet files found)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=2000,
        help="Rows per INSERT batch (default: 2000)",
    )
    return p.parse_args()


def _discover(
    data_dir: Path, symbols: list[str] | None, intervals: list[str] | None
) -> Generator[tuple[str, str, Path]]:
    """Yield (symbol, interval, path) for every matching Parquet file."""
    if not data_dir.exists():
        logger.error("data-dir %s does not exist", data_dir)
        return

    sym_dirs = sorted(
        d for d in data_dir.iterdir() if d.is_dir() and (symbols is None or d.name in symbols)
    )
    for sym_dir in sym_dirs:
        for parquet in sorted(sym_dir.glob("*.parquet")):
            interval = parquet.stem  # e.g. "15min", "day"
            if intervals is not None and interval not in intervals:
                continue
            yield sym_dir.name, interval, parquet


def _load_parquet(symbol: str, interval: str, path: Path) -> list[CandleRow]:
    """Read a Parquet file and return a list of candle dicts."""
    try:
        df = pl.read_parquet(path)
    except Exception as exc:
        logger.warning("import-candles: cannot read %s — %s", path, exc)
        return []

    # Normalise the date column name
    if "date" not in df.columns and "timestamp" in df.columns:
        df = df.rename({"timestamp": "date"})
    if "date" not in df.columns:
        logger.warning("import-candles: %s has no date/timestamp column — skipping", path)
        return []

    required = {"open", "high", "low", "close"}
    if not required.issubset(df.columns):
        logger.warning(
            "import-candles: %s missing columns %s — skipping",
            path,
            required - set(df.columns),
        )
        return []

    if "volume" not in df.columns:
        df = df.with_columns(pl.lit(0).alias("volume"))

    rows: list[CandleRow] = []
    for row in df.iter_rows(named=True):
        ts = row["date"]
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        elif not hasattr(ts, "tzinfo"):
            continue  # skip non-datetime rows
        rows.append(
            CandleRow(
                symbol=symbol,
                interval=interval,
                ts=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row.get("volume") or 0),
            )
        )
    return rows


async def _run(args: argparse.Namespace) -> None:
    from trading.config.settings import get_settings
    from trading.app.database import build_engine, build_session_factory, init_db
    from trading.candles.storage.store import CandleDataStore

    settings = get_settings()
    engine = build_engine(str(settings.postgres_url))
    await init_db(engine)
    sf = build_session_factory(engine)
    candle_store = CandleDataStore(sf)

    total_inserted = 0
    for symbol, interval, path in _discover(args.data_dir, args.symbols, args.intervals):
        rows = _load_parquet(symbol, interval, path)
        if not rows:
            continue

        inserted = 0
        for i in range(0, len(rows), args.batch_size):
            batch = rows[i : i + args.batch_size]
            await candle_store.save_candles(batch)
            inserted += len(batch)

        logger.info(
            "import-candles: %s/%s — %d rows inserted from %s",
            symbol,
            interval,
            inserted,
            path.name,
        )
        total_inserted += inserted

    logger.info("import-candles: done — %d total rows inserted", total_inserted)
    await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args()
    anyio.run(_run, args)


if __name__ == "__main__":
    main()
