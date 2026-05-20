"""Fixtures shared by indicator integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from testing.backtesting.data_loader import BrokerDataLoader, FileDataLoader
from testing.simulators.synthetic_broker import SyntheticDataBroker

from trading.core.clock import SimulatedClock
from quantindicators.polars_store import PolarsStore

# trading-platform/data/  (parents: [0]=indicators [1]=strategy-testing(tests)
#                                    [2]=strategy-testing(pkg) [3]=integ
#                                    [4]=tst [5]=trading-platform)
_DATA_DIR = Path(__file__).parents[5] / "data"

_DEFAULT_SYMBOL = "INFY"
_DEFAULT_INTERVAL = "15min"
_MONTH_END = datetime(2026, 4, 17, tzinfo=UTC)
_MONTH_START = _MONTH_END - timedelta(days=30)


# ---------------------------------------------------------------------------
# CLI option
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--data-source",
        default="synthetic",
        choices=["synthetic", "parquet"],
        help="Data source for indicator tests (default: synthetic, no files needed).",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def data_source(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--data-source")


@pytest.fixture
def simulated_clock() -> SimulatedClock:
    return SimulatedClock()


@pytest.fixture
def data_loader(data_source: str):
    """
    DataLoader backed by SyntheticDataBroker (default) or real Parquet files.

    Both return a pl.DataFrame with the same schema — tests see no difference.
    Switch data source via: uv run pytest --data-source=parquet
    """
    if data_source == "parquet":
        return FileDataLoader(_DATA_DIR)
    return BrokerDataLoader(SyntheticDataBroker())


@pytest.fixture
def make_store(data_loader, simulated_clock: SimulatedClock):
    """
    Factory fixture: call make_store(symbol, interval) → (PolarsStore, list[dict]).

    Loads bars for the given date window, pushes every bar into a fresh
    PolarsStore (advancing the simulated clock per bar), and returns the
    populated store alongside the raw row list for tests that need it.
    """

    def _factory(
        symbol: str = _DEFAULT_SYMBOL,
        interval: str = _DEFAULT_INTERVAL,
        start: datetime = _MONTH_START,
        end: datetime = _MONTH_END,
    ) -> tuple[PolarsStore, list[dict]]:
        df = data_loader.load(symbol, interval, start, end)
        store = PolarsStore(maxlen=500)
        rows: list[dict] = []
        for row in df.to_dicts():
            r = {**row, "ts": row["date"]}
            simulated_clock.advance(r["ts"])
            store.push(symbol, interval, r)
            rows.append(r)
        return store, rows

    return _factory
