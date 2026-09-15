"""Tests for CandleAggregator lifecycle and warmup replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import polars as pl
import pytest
from anyio import create_task_group, sleep
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trading.app.database import init_db
from trading.candles.service.aggregator import CandleAggregator, CandleAggregatorComponent
from trading.candles.service.bar_accumulator import SymbolConfig
from trading.candles.service.historical import HistoricalDataResult, HistoricalDataService
from trading.candles.service.persister import CandleConfig
from trading.candles.storage.models import Instrument
from trading.core.lifecycle.component import ComponentState
from trading.core.schemas import CandleEvent, InstrumentType, TickEvent

_INFY_SYMBOL = SymbolConfig(
    symbol="INFY",
    instrument_token=1,
    instrument_type=InstrumentType.EQUITY,
)
_RELIANCE_SYMBOL = SymbolConfig(
    symbol="RELIANCE",
    instrument_token=2,
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
    symbols: list[SymbolConfig] | None = None,
) -> CandleAggregatorComponent:
    return CandleAggregatorComponent(
        candle_aggregator=candle_registry or MagicMock(),
        historical_data_service=service,
        symbols=symbols if symbols is not None else [_INFY_SYMBOL],
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
    from trading.app.database import get_session

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
    mock_algo_reg.restore_state = AsyncMock()

    agg = _make_component(_stub_service([candle]))
    agg.add_algo_registry(mock_algo_reg)

    await agg._setup()

    mock_algo_reg.handle.assert_called_once()
    called_candle = mock_algo_reg.handle.call_args[0][0]
    assert called_candle.symbol == "INFY"
    assert called_candle.close == pytest.approx(103.0)
    mock_algo_reg.restore_state.assert_called_once_with()


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
    mock_algo_reg.restore_state = AsyncMock()

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
    mock_algo_reg.restore_state = AsyncMock()

    agg = _make_component(_stub_service([]))
    agg.add_algo_registry(mock_algo_reg)

    await agg._setup()

    mock_algo_reg.handle.assert_not_called()
    mock_algo_reg.restore_state.assert_called_once_with()


# ---------------------------------------------------------------------------
# CandleAggregatorComponent.rewarm_after_login() (trading-platform#40)
# ---------------------------------------------------------------------------


def _make_consumer(needing_rewarm: set[str]) -> MagicMock:
    consumer = MagicMock()
    consumer.symbols_needing_rewarm = MagicMock(return_value=needing_rewarm)
    consumer.rewarm = MagicMock()
    consumer.restore_state = AsyncMock()
    return consumer


async def test_rewarm_after_login_skips_fetch_when_no_symbols_untouched(
    engine: AsyncEngine,
) -> None:
    service = _stub_service()
    agg = _make_component(service)
    agg.add_algo_registry(_make_consumer(set()))

    await agg.rewarm_after_login()

    service.fetch.assert_not_called()  # type: ignore[attr-defined]


async def test_rewarm_after_login_fetches_only_untouched_symbols(engine: AsyncEngine) -> None:
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
    service = _stub_service([candle])
    agg = _make_component(service)
    consumer = _make_consumer({"INFY"})
    agg.add_algo_registry(consumer)

    await agg.rewarm_after_login()

    service.fetch.assert_called_once()  # type: ignore[attr-defined]
    consumer.rewarm.assert_called_once()
    (candles_by_symbol,) = consumer.rewarm.call_args[0]
    assert list(candles_by_symbol) == ["INFY"]
    assert candles_by_symbol["INFY"][0].close == pytest.approx(103.0)
    consumer.restore_state.assert_called_once_with(symbols={"INFY"})


async def test_rewarm_after_login_unions_untouched_symbols_across_consumers(
    engine: AsyncEngine,
) -> None:
    service = _stub_service([])
    agg = _make_component(service)
    agg.add_algo_registry(_make_consumer({"INFY"}))
    agg.add_algo_registry(_make_consumer(set()))

    await agg.rewarm_after_login()


async def test_rewarm_after_login_restore_state_scoped_per_consumer_not_shared_union(
    engine: AsyncEngine,
) -> None:
    """trading-platform#79: restore_state() must be called with THIS consumer's own
    symbols_needing_rewarm() intersected with fetched candles -- never the raw
    cross-consumer `untouched` union. A symbol pending for one consumer (INFY here)
    but already live for another (RELIANCE, bars_seen>0 for consumer_b) must not
    have consumer_b's restore_state() invoked for it -- that would risk clobbering
    real live-tick-derived state with a stale/irrelevant restore (the #40 class of
    regression)."""
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
    # _fetch_warmup_candles tags each fetched row with the SymbolConfig it was
    # requested for, not whatever's in the stubbed df -- one candle's worth of
    # stubbed data is enough to get a non-empty result for both symbols below.
    service = _stub_service([candle])
    agg = _make_component(service, symbols=[_INFY_SYMBOL, _RELIANCE_SYMBOL])
    # consumer_a still needs both symbols re-warmed.
    consumer_a = _make_consumer({"INFY", "RELIANCE"})
    # consumer_b only still needs INFY -- RELIANCE is already live for it.
    consumer_b = _make_consumer({"INFY"})
    agg.add_algo_registry(consumer_a)
    agg.add_algo_registry(consumer_b)

    await agg.rewarm_after_login()

    consumer_a.restore_state.assert_called_once_with(symbols={"INFY", "RELIANCE"})
    consumer_b.restore_state.assert_called_once_with(symbols={"INFY"})


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


async def test_handle_returns_empty_when_no_bar_closes() -> None:
    agg, _ = _make_aggregator(accumulator_result=None)
    result = await agg.handle(_make_tick())
    assert result == []


async def test_handle_returns_candle_when_bar_closes() -> None:
    candle = _make_candle()
    agg, _ = _make_aggregator(accumulator_result=candle)
    result = await agg.handle(_make_tick())
    assert result == [candle]


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


async def test_handle_unknown_token_returns_empty() -> None:
    agg, mock_logger = _make_aggregator(accumulator_result=_make_candle())
    result = await agg.handle(_make_tick(token=999))
    assert result == []
    await sleep(0)
    assert len(mock_logger.calls) == 0


def _make_multi_interval_aggregator(
    logger: _MockCandleLogger | None = None,
) -> tuple[CandleAggregator, _MockCandleLogger]:
    """Aggregator configured with two intervals, both closing on the same tick."""
    mock_logger = logger or _MockCandleLogger()
    candle_1min = _make_candle()
    candle_5min = CandleEvent(
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        interval="5min",
        open=100.0,
        high=106.0,
        low=98.0,
        close=104.0,
        volume=2000,
        timestamp=datetime(2025, 1, 6, 9, 15, tzinfo=UTC),
        tick_log_id=0,
    )
    mock_acc = MagicMock()
    mock_acc.process = MagicMock(side_effect=[candle_1min, candle_5min])
    config = CandleConfig(
        instruments=[Instrument(token=1, symbol="INFY", exchange="NSE", instrument_type="EQUITY")],
        intervals=["1min", "5min"],
        warmup_count=5,
    )
    agg = CandleAggregator(config=config, candle_logger=mock_logger, accumulator=mock_acc)
    return agg, mock_logger


async def test_handle_returns_all_intervals_that_close_on_same_tick() -> None:
    agg, mock_logger = _make_multi_interval_aggregator()
    result = await agg.handle(_make_tick())

    assert len(result) == 2
    assert {c.interval for c in result} == {"1min", "5min"}

    await sleep(0)
    assert len(mock_logger.calls) == 2
