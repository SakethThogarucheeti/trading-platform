"""Tests for CandleAggregator lifecycle and warmup replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import polars as pl
import pytest
from anyio import create_task_group, sleep
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.broker.base.broker import Broker
from trading.candles.bar_accumulator import SymbolConfig
from trading.candles.historical_data_service import HistoricalDataResult, HistoricalDataService
from trading.core.database import build_session_factory, init_db
from trading.core.models import Instrument
from trading.core.schemas import CandleEvent, InstrumentType, TickEvent
from trading.candles.candle_aggregator import CandleAggregator, CandleAggregatorComponent, CandleConfig
from trading.core.lifecycle.component import ComponentState
from trading.storage.stores.audit import AuditStore
from trading.storage.stores.candle import CandleDataStore


_INFY_SYMBOL = SymbolConfig(
    symbol="INFY",
    instrument_token=1,
    instrument_type=InstrumentType.EQUITY,
)


def _stub_service(candles: list[CandleEvent] | None = None) -> HistoricalDataService:
    """Build a HistoricalDataService mock that returns the given candles as a DataFrame."""
    service = MagicMock(spec=HistoricalDataService)

    if candles:
        df = pl.DataFrame(
            {
                "date": [c.timestamp for c in candles],
                "open": [c.open for c in candles],
                "high": [c.high for c in candles],
                "low": [c.low for c in candles],
                "close": [c.close for c in candles],
                "volume": [c.volume for c in candles],
            }
        )
    else:
        df = pl.DataFrame(
            schema={
                "date": pl.Datetime("us", "UTC"),
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Int64,
            }
        )

    service.fetch = AsyncMock(return_value=HistoricalDataResult(df=df, fetched_from_broker=False))
    return service


def _make_component(
    service: HistoricalDataService,
    candle_registry: object | None = None,
    warmup_count: int = 5,
) -> CandleAggregatorComponent:
    return CandleAggregatorComponent(
        candle_aggregator=candle_registry or MagicMock(),
        historical_data_service=service,
        symbols=[_INFY_SYMBOL],
        intervals=["1min"],
        warmup_count=warmup_count,
    )


@pytest.fixture
async def engine() -> AsyncEngine:  # type: ignore[misc]
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(eng)
    yield eng
    await eng.dispose()


# ---------------------------------------------------------------------------
# CandleAggregatorComponent lifecycle
# ---------------------------------------------------------------------------


async def test_candle_aggregator_starts_and_reaches_running(engine: AsyncEngine) -> None:
    sf = build_session_factory(engine)

    from trading.core.database import get_session

    async with get_session(engine) as s:
        s.add(Instrument(token=1, symbol="INFY", exchange="NSE", instrument_type="EQUITY"))

    config = CandleConfig(
        instruments=[Instrument(token=1, symbol="INFY", exchange="NSE", instrument_type="EQUITY")],
        intervals=["1min"],
        warmup_count=5,
    )
    reg = CandleAggregator(
        config=config,
        candle_logger=MagicMock(),
    )
    agg = _make_component(_stub_service(), reg)

    async with create_task_group() as tg:
        await tg.start(agg.start)
        await sleep(0.1)
        assert agg.state == ComponentState.RUNNING
        await agg.stop()


async def test_candle_aggregator_add_algo_registry_and_warmup_replay(
    engine: AsyncEngine,
) -> None:
    """add_algo_registry registers a callback and warmup candles are replayed."""
    candle = CandleEvent(
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        interval="1min",
        open=100.0,
        high=105.0,
        low=99.0,
        close=103.0,
        volume=1000,
        timestamp=datetime(2025, 1, 6, 9, 15, tzinfo=UTC),
        tick_log_id=0,
    )

    mock_algo_reg = MagicMock()
    mock_algo_reg.setup = MagicMock()
    mock_algo_reg.handle = AsyncMock(return_value=[])

    agg = _make_component(_stub_service([candle]))
    agg.add_algo_registry(mock_algo_reg)

    await agg._setup()

    mock_algo_reg.handle.assert_called_once()
    called_candle = mock_algo_reg.handle.call_args[0][0]
    assert called_candle.symbol == "INFY"
    assert called_candle.close == pytest.approx(103.0)


async def test_candle_aggregator_warmup_error_does_not_abort(
    engine: AsyncEngine,
) -> None:
    """An exception in a warmup replay call is logged but does not propagate."""
    candle = CandleEvent(
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        interval="1min",
        open=100.0,
        high=105.0,
        low=99.0,
        close=103.0,
        volume=1000,
        timestamp=datetime(2025, 1, 6, 9, 15, tzinfo=UTC),
        tick_log_id=0,
    )

    mock_algo_reg = MagicMock()
    mock_algo_reg.setup = MagicMock()
    mock_algo_reg.handle = AsyncMock(side_effect=RuntimeError("boom"))

    agg = _make_component(_stub_service([candle]))
    agg.add_algo_registry(mock_algo_reg)

    await agg._setup()  # must not raise


async def test_candle_aggregator_no_warmup_candles_no_replay(
    engine: AsyncEngine,
) -> None:
    """When service returns an empty DataFrame, no algo handles are called."""
    mock_algo_reg = MagicMock()
    mock_algo_reg.setup = MagicMock()
    mock_algo_reg.handle = AsyncMock(return_value=[])

    agg = _make_component(_stub_service([]))
    agg.add_algo_registry(mock_algo_reg)

    await agg._setup()

    mock_algo_reg.handle.assert_not_called()


# ---------------------------------------------------------------------------
# CandleAggregator.handle() — pure unit tests (no DB)
# ---------------------------------------------------------------------------


@dataclass
class _MockCandleLogger:
    calls: list[CandleEvent] = field(default_factory=list)

    async def log(self, event: CandleEvent) -> None:
        self.calls.append(event)


def _make_tick(token: int = 1, price: float = 100.0) -> TickEvent:
    return TickEvent(
        instrument_token=token,
        instrument_type=InstrumentType.EQUITY,
        last_price=price,
        volume=500,
        timestamp=datetime(2025, 1, 6, 9, 15, tzinfo=UTC),
        tick_log_id=0,
    )


def _make_candle(symbol: str = "INFY") -> CandleEvent:
    return CandleEvent(
        symbol=symbol,
        instrument_type=InstrumentType.EQUITY,
        interval="1min",
        open=100.0,
        high=105.0,
        low=99.0,
        close=103.0,
        volume=1000,
        timestamp=datetime(2025, 1, 6, 9, 15, tzinfo=UTC),
        tick_log_id=0,
    )


def _make_aggregator(
    logger: _MockCandleLogger | None = None,
    accumulator_result: CandleEvent | None = None,
) -> tuple[CandleAggregator, _MockCandleLogger]:
    mock_logger = logger or _MockCandleLogger()
    mock_acc = MagicMock()
    mock_acc.process = MagicMock(return_value=accumulator_result)
    config = CandleConfig(
        instruments=[Instrument(token=1, symbol="INFY", exchange="NSE", instrument_type="EQUITY")],
        intervals=["1min"],
        warmup_count=5,
    )
    agg = CandleAggregator(config=config, candle_logger=mock_logger, accumulator=mock_acc)
    return agg, mock_logger


async def test_handle_returns_none_when_no_bar_closes() -> None:
    agg, _ = _make_aggregator(accumulator_result=None)
    result = await agg.handle(_make_tick())
    assert result is None


async def test_handle_returns_candle_when_bar_closes() -> None:
    candle = _make_candle()
    agg, _ = _make_aggregator(accumulator_result=candle)
    result = await agg.handle(_make_tick())
    assert result is candle


async def test_handle_calls_logger_on_bar_close() -> None:
    candle = _make_candle()
    agg, mock_logger = _make_aggregator(accumulator_result=candle)
    await agg.handle(_make_tick())
    # fire() schedules log() as a background task; give the event loop a turn
    await sleep(0)
    assert len(mock_logger.calls) == 1
    assert mock_logger.calls[0] is candle


async def test_handle_no_logger_call_when_bar_open() -> None:
    agg, mock_logger = _make_aggregator(accumulator_result=None)
    await agg.handle(_make_tick())
    await sleep(0)
    assert len(mock_logger.calls) == 0


async def test_handle_unknown_token_returns_none() -> None:
    agg, mock_logger = _make_aggregator(accumulator_result=_make_candle())
    result = await agg.handle(_make_tick(token=999))
    assert result is None
    await sleep(0)
    assert len(mock_logger.calls) == 0
