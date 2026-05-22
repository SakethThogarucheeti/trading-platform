"""Tests for core/schemas.py"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from trading.core.schemas import (
    CandleEvent,
    FillEvent,
    InstrumentType,
    OrderEvent,
    OrderStatus,
    OrderType,
    Side,
    SignalEvent,
    SignalType,
    TickEvent,
    ValidatedOrderEvent,
)

NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# TickEvent
# ---------------------------------------------------------------------------


def test_tick_event_valid() -> None:
    t = TickEvent(
        instrument_token=123,
        instrument_type=InstrumentType.EQUITY,
        last_price=100.5,
        volume=1000,
        timestamp=NOW,
        tick_log_id=1,
    )
    assert t.last_price == 100.5


def test_tick_event_zero_price_raises() -> None:
    with pytest.raises(ValidationError):
        TickEvent(
            instrument_token=1,
            instrument_type=InstrumentType.EQUITY,
            last_price=0.0,
            volume=0,
            timestamp=NOW,
            tick_log_id=1,
        )


def test_tick_event_negative_price_raises() -> None:
    with pytest.raises(ValidationError):
        TickEvent(
            instrument_token=1,
            instrument_type=InstrumentType.EQUITY,
            last_price=-1.0,
            volume=0,
            timestamp=NOW,
            tick_log_id=1,
        )


def test_tick_event_zero_volume_allowed() -> None:
    t = TickEvent(
        instrument_token=1,
        instrument_type=InstrumentType.EQUITY,
        last_price=10.0,
        volume=0,
        timestamp=NOW,
        tick_log_id=1,
    )
    assert t.volume == 0


def test_tick_event_invalid_instrument_type_raises() -> None:
    with pytest.raises(ValidationError):
        TickEvent(
            instrument_token=1,
            instrument_type="BOND",  # type: ignore[arg-type]
            last_price=10.0,
            volume=0,
            timestamp=NOW,
            tick_log_id=1,
        )


# ---------------------------------------------------------------------------
# CandleEvent
# ---------------------------------------------------------------------------


def test_candle_event_valid() -> None:
    c = CandleEvent(
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        interval="5min",
        open=100.0,
        high=110.0,
        low=99.0,
        close=105.0,
        volume=5000,
        timestamp=NOW,
        tick_log_id=1,
    )
    assert c.symbol == "INFY"


def test_candle_event_zero_open_raises() -> None:
    with pytest.raises(ValidationError):
        CandleEvent(
            symbol="INFY",
            instrument_type=InstrumentType.EQUITY,
            interval="5min",
            open=0.0,
            high=110.0,
            low=99.0,
            close=105.0,
            volume=0,
            timestamp=NOW,
            tick_log_id=1,
        )


# ---------------------------------------------------------------------------
# SignalEvent
# ---------------------------------------------------------------------------


def test_signal_event_autogenerates_signal_id() -> None:
    s = SignalEvent(
        symbol="TCS",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        strategy_id="ema_cross",
        signal_type=SignalType.ENTRY,
        stop_distance=5.0,
        tick_log_id=1,
    )
    assert isinstance(s.signal_id, UUID)


def test_signal_event_unique_ids() -> None:
    s1 = SignalEvent(
        symbol="TCS",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        strategy_id="s",
        signal_type=SignalType.ENTRY,
        stop_distance=1.0,
        tick_log_id=1,
    )
    s2 = SignalEvent(
        symbol="TCS",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        strategy_id="s",
        signal_type=SignalType.ENTRY,
        stop_distance=1.0,
        tick_log_id=1,
    )
    assert s1.signal_id != s2.signal_id


def test_signal_event_zero_stop_distance_raises() -> None:
    with pytest.raises(ValidationError):
        SignalEvent(
            symbol="TCS",
            instrument_type=InstrumentType.EQUITY,
            side=Side.BUY,
            strategy_id="s",
            signal_type=SignalType.ENTRY,
            stop_distance=0.0,
            tick_log_id=1,
        )


def test_signal_event_autogenerates_timestamp() -> None:
    s = SignalEvent(
        symbol="TCS",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        strategy_id="s",
        signal_type=SignalType.ENTRY,
        stop_distance=1.0,
        tick_log_id=1,
    )
    assert isinstance(s.timestamp, datetime)


# ---------------------------------------------------------------------------
# ValidatedOrderEvent
# ---------------------------------------------------------------------------


def test_validated_order_zero_quantity_raises() -> None:
    with pytest.raises(ValidationError):
        ValidatedOrderEvent(
            signal_id=UUID("12345678-1234-5678-1234-567812345678"),
            symbol="INFY",
            instrument_type=InstrumentType.EQUITY,
            side=Side.BUY,
            quantity=0,
            order_type=OrderType.MARKET,
            tick_log_id=1,
        )


def test_validated_order_market_limit_price_none_allowed() -> None:
    v = ValidatedOrderEvent(
        signal_id=UUID("12345678-1234-5678-1234-567812345678"),
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        tick_log_id=1,
    )
    assert v.limit_price is None


# ---------------------------------------------------------------------------
# OrderEvent / FillEvent
# ---------------------------------------------------------------------------


def test_order_event_valid() -> None:
    o = OrderEvent(
        signal_id=UUID("12345678-1234-5678-1234-567812345678"),
        kite_order_id="ORD001",
        status=OrderStatus.PLACED,
    )
    assert o.kite_order_id == "ORD001"


def test_fill_event_zero_price_raises() -> None:
    with pytest.raises(ValidationError):
        FillEvent(
            kite_order_id="ORD001",
            avg_price=0.0,
            filled_qty=10,
            timestamp=NOW,
        )


def test_fill_event_zero_qty_raises() -> None:
    with pytest.raises(ValidationError):
        FillEvent(
            kite_order_id="ORD001",
            avg_price=100.0,
            filled_qty=0,
            timestamp=NOW,
        )


# ---------------------------------------------------------------------------
# Round-trip serialisation
# ---------------------------------------------------------------------------


def test_tick_event_round_trip() -> None:
    original = TickEvent(
        instrument_token=42,
        instrument_type=InstrumentType.FUTURES,
        last_price=250.75,
        volume=300,
        timestamp=NOW,
        tick_log_id=1,
    )
    restored = TickEvent.model_validate_json(original.model_dump_json())
    assert restored.instrument_token == original.instrument_token
    assert restored.last_price == original.last_price
    assert restored.instrument_type == original.instrument_type


def test_signal_event_round_trip() -> None:
    original = SignalEvent(
        symbol="RELIANCE",
        instrument_type=InstrumentType.EQUITY,
        side=Side.SELL,
        strategy_id="rsi_diverge",
        signal_type=SignalType.EXIT,
        stop_distance=12.5,
        tick_log_id=1,
    )
    restored = SignalEvent.model_validate_json(original.model_dump_json())
    assert restored.signal_id == original.signal_id
    assert restored.stop_distance == original.stop_distance


def test_signal_event_algo_name_defaults_none() -> None:
    s = SignalEvent(
        symbol="TCS",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        strategy_id="s",
        signal_type=SignalType.ENTRY,
        stop_distance=1.0,
        tick_log_id=1,
    )
    assert s.algo_name is None


def test_signal_event_algo_name_preserved_in_round_trip() -> None:
    original = SignalEvent(
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        strategy_id="ema_cross",
        algo_name="my_algo",
        signal_type=SignalType.ENTRY,
        stop_distance=5.0,
        tick_log_id=1,
    )
    restored = SignalEvent.model_validate_json(original.model_dump_json())
    assert restored.algo_name == "my_algo"


def test_signal_event_from_signal_propagates_algo_name() -> None:
    from trading.core.schemas import CandleEvent

    base = SignalEvent(
        symbol="INFY",
        instrument_type=InstrumentType.EQUITY,
        side=Side.BUY,
        strategy_id="ema_cross",
        signal_type=SignalType.ENTRY,
        stop_distance=5.0,
        tick_log_id=1,
    )
    copied = SignalEvent.from_signal(base, tick_log_id=42, algo_name="ema_algo")
    assert copied.algo_name == "ema_algo"
    assert copied.tick_log_id == 42
    assert copied.symbol == "INFY"
