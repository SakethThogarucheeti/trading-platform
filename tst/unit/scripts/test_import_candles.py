"""
Baseline coverage for import_candles._load_parquet -- constructs small
in-memory Parquet files via polars so no real data/ directory is needed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from trading.scripts.import_candles import _load_parquet


def _write_parquet(tmp_path, name: str, df: pl.DataFrame):
    path = tmp_path / name
    df.write_parquet(path)
    return path


def test_load_parquet_returns_candle_rows(tmp_path):
    df = pl.DataFrame(
        {
            "date": [datetime(2024, 1, 2, 9, 15, tzinfo=UTC)],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "volume": [1000],
        }
    )
    path = _write_parquet(tmp_path, "15min.parquet", df)

    rows = _load_parquet("INFY", "15min", path)

    assert len(rows) == 1
    assert rows[0]["symbol"] == "INFY"
    assert rows[0]["interval"] == "15min"
    assert rows[0]["open"] == 100.0
    assert rows[0]["volume"] == 1000


def test_load_parquet_renames_timestamp_column_to_date(tmp_path):
    df = pl.DataFrame(
        {
            "timestamp": [datetime(2024, 1, 2, 9, 15, tzinfo=UTC)],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
        }
    )
    path = _write_parquet(tmp_path, "day.parquet", df)

    rows = _load_parquet("INFY", "day", path)

    assert len(rows) == 1


def test_load_parquet_defaults_missing_volume_to_zero(tmp_path):
    df = pl.DataFrame(
        {
            "date": [datetime(2024, 1, 2, 9, 15, tzinfo=UTC)],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
        }
    )
    path = _write_parquet(tmp_path, "day.parquet", df)

    rows = _load_parquet("INFY", "day", path)

    assert rows[0]["volume"] == 0


def test_load_parquet_attaches_utc_to_naive_timestamps(tmp_path):
    df = pl.DataFrame(
        {
            "date": [datetime(2024, 1, 2, 9, 15)],  # naive, no tzinfo
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
        }
    )
    path = _write_parquet(tmp_path, "day.parquet", df)

    rows = _load_parquet("INFY", "day", path)

    assert len(rows) == 1
    assert rows[0]["ts"].tzinfo is not None


def test_load_parquet_returns_empty_when_no_date_column(tmp_path):
    df = pl.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5]})
    path = _write_parquet(tmp_path, "day.parquet", df)

    rows = _load_parquet("INFY", "day", path)

    assert rows == []


def test_load_parquet_returns_empty_when_missing_ohlc_column(tmp_path):
    df = pl.DataFrame(
        {
            "date": [datetime(2024, 1, 2, 9, 15, tzinfo=UTC)],
            "open": [100.0],
            "high": [101.0],
            # missing low, close
        }
    )
    path = _write_parquet(tmp_path, "day.parquet", df)

    rows = _load_parquet("INFY", "day", path)

    assert rows == []


def test_load_parquet_returns_empty_when_file_unreadable(tmp_path):
    path = tmp_path / "not_parquet.parquet"
    path.write_text("this is not a parquet file")

    rows = _load_parquet("INFY", "day", path)

    assert rows == []
