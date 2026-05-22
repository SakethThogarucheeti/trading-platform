"""Tests for di/providers/indicators.py — make_candle_store."""

from __future__ import annotations

from unittest.mock import MagicMock

from trading.di.providers.indicators import make_candle_store
from trading.storage.stores.candle_store import CandleStore


def test_make_candle_store_without_redis() -> None:
    candle_data = MagicMock()
    store = make_candle_store(candle_store=candle_data)
    assert isinstance(store, CandleStore)
    assert store._redis is None


def test_make_candle_store_with_redis() -> None:
    candle_data = MagicMock()
    redis = MagicMock()
    store = make_candle_store(candle_store=candle_data, redis=redis)
    assert isinstance(store, CandleStore)
    assert store._redis is redis
