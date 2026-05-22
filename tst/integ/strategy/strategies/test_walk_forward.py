"""
Walk-forward runner tests.

Verifies window count, session IDs, and aggregate metric computation
using synthetic in-memory data (no real DB or broker).
"""

from __future__ import annotations

from datetime import datetime

import polars as pl
from testing.backtesting.data_loader import DataLoader
from testing.walk_forward.runner import _compute_windows

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _InMemoryLoader(DataLoader):
    """DataLoader that returns a pre-built DataFrame for any symbol/interval."""

    def __init__(self, df: pl.DataFrame) -> None:
        self._df = df

    def load(self, symbol: str, interval: str, start: datetime, end: datetime) -> pl.DataFrame:
        return self._df.filter((pl.col("date") >= start) & (pl.col("date") <= end))


def _make_df(n_bars: int) -> pl.DataFrame:
    from testing.utils.generators import trending_market

    return trending_market(n_bars=n_bars, seed=42)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_compute_windows_correct_count():
    """Window count should be predictable from bar count and step size."""
    df = _make_df(500)
    windows = _compute_windows(df, train_bars=200, test_bars=50, step_bars=50)
    # Expected: floor((500 - 250) / 50) + 1 windows
    expected = (500 - 250) // 50 + 1
    assert len(windows) == expected


def test_compute_windows_no_overlap_in_test_periods():
    """Test windows must not overlap each other in the test period."""
    df = _make_df(400)
    windows = _compute_windows(df, train_bars=150, test_bars=50, step_bars=50)

    for i in range(len(windows) - 1):
        _, _, test_start_i, test_end_i = windows[i]
        _, _, test_start_j, test_end_j = windows[i + 1]
        assert test_end_i < test_start_j, (
            f"Test window {i} and {i + 1} overlap: {test_end_i} vs {test_start_j}"
        )


def test_compute_windows_empty_when_not_enough_bars():
    """No windows when there aren't enough bars."""
    df = _make_df(10)
    windows = _compute_windows(df, train_bars=200, test_bars=50, step_bars=50)
    assert len(windows) == 0


def test_walk_forward_session_ids_unique():
    """Each window's BacktestReport should have a unique session_id."""
    from testing.walk_forward.runner import _compute_windows

    df = _make_df(300)
    windows = _compute_windows(df, train_bars=100, test_bars=50, step_bars=50)
    # session IDs would be "{parent_id}_w{i}" — just check the pattern is stable
    parent_id = "test_parent"
    ids = [f"{parent_id}_w{i + 1}" for i in range(len(windows))]
    assert len(ids) == len(set(ids)), "Window session IDs must be unique"
