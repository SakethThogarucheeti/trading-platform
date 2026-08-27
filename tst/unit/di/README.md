# tst/unit/di/

Unit tests for `src/trading/di/`.

## Files

| File | What it tests |
|------|--------------|
| `test_container.py` | DI container resolution: `Settings`, `AsyncEngine`, `async_sessionmaker`, `TradingStore`, `AuditStore` |
| `test_indicators_provider.py` | `CandleStore` provider construction |
| `test_providers.py` | Provider isolation — verifies `MockBrokerProvider` can replace `BrokerProvider` without breaking other providers |
