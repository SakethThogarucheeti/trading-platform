# tst/unit/di/

Unit tests for `src/trading/di/`.

## Files

| File | What it tests |
|------|--------------|
| `test_container.py` | DI container resolution: `Settings`, `AsyncEngine`, `async_sessionmaker`, `TradingStore`, `AuditStore` |
| `test_indicators_provider.py` | `CandleStore` provider — verifies Redis cache is wired when a Redis client is present, bypassed when absent |
| `test_providers.py` | Provider isolation — verifies `MockBrokerProvider` can replace `BrokerProvider` without breaking other providers |
