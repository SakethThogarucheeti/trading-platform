# tst/unit/indicators/

Unit tests for the indicator storage layer (`src/trading/storage/stores/candle_store.py` and `candle.py`).

## Files

| File | What it tests |
|------|--------------|
| `test_store.py` | `CandleDataStore` — save and retrieve OHLCV candles; `ON CONFLICT DO NOTHING` idempotency |
| `test_candle_store_redis.py` | `CandleStore` Redis caching — cache hit/miss behavior, TTL expiry, fallback to Postgres when Redis is absent |
