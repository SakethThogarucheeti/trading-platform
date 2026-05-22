# tst/unit/ — Unit tests

Fast, isolated tests. No Docker, no network, no external services. Run from the project root:

```bash
uv run pytest tst/unit/ -x -q
```

## Layout

The directory structure mirrors `src/trading/`. To find tests for a package, look in the corresponding directory here.

| Directory | Tests for |
|-----------|-----------|
| `api/dashboard/` | FastAPI dashboard endpoints + `DashboardServer` component |
| `broker/` | `Broker` ABC, `PaperBroker`, `PriceStore` |
| `candles/` | `BarAccumulator`, `CandleAggregator` |
| `config/` | `Settings`, `StrategyConfig` |
| `core/` | schemas, models, clock, pipeline, messaging, tasks |
| `core/lifecycle/` | `Component` ABC, `Runtime` supervisor |
| `di/` | DI container, provider wiring |
| `execution/` | `OrderExecutor`, idempotency |
| `indicators/` | `CandleStore`, Redis candle store |
| `monitoring/` | `Scheduler`, `HeartbeatMonitor` |
| `reports/` | P&L calculation, report engine |
| `risk/` | `RiskFilter`, `calculate_quantity`, `RiskController` |
| `storage/` | All domain stores (TradingStore, AuditStore, etc.) |
| `strategy/` | Strategy base, factory, signal generator, all built-in strategies |
| `tick_ingest/` | `TickIngestor`, `KiteIngestor`, `TickPublisher`, `CircuitBreaker` |
| `worker/` | `TickSubscriber`, `RedisCircuitBreaker` |

## Conventions

- No real broker, DB, or Redis connections — use `fakeredis`, `AsyncMock`, or in-memory SQLite.
- `SimulatedClock` replaces `SystemClock` for deterministic timestamps.
- Each test file is a peer of the module it tests (`test_bar_accumulator.py` tests `candles/bar_accumulator.py`).
