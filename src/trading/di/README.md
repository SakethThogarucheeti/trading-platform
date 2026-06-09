# di

Global dependency injection layer. Uses [Dishka](https://github.com/reagento/dishka) — type-based async DI.

## How it works

`trading.app.container` calls `make_async_container(...)` with all providers. Dishka resolves types lazily: when a component requests `TradingStore`, it walks the provider graph to find the `@provide` method that returns that type, runs it, and caches the result for the container's lifetime.

Each module has its own `di/providers.py` that wires that module's internals. The global `di/providers/` here wires cross-cutting concerns and the full pipeline.

## Providers

| File | What it provides |
|------|-----------------|
| `infra.py` | DB engine, session factory, all store instances (one per module) |
| `broker.py` | `Broker` + `BrokerStream` (live or paper based on `Settings.paper_trading`) |
| `components.py` | `KiteIngestor`, `CandleAggregator`, `SignalGenerator` per algo, `HeartbeatMonitor` |
| `algo_pipeline.py` | `RiskFilter` and `OrderExecutor` per algo — wired into `AlgoPipeline` |
| `worker_components.py` | Worker-process variants of the same components (no HTTP server) |
| `indicators.py` | `CandleStore` — the `AbstractCandleStore` implementation backed by Postgres + optional Redis cache |
| `strategy.py` | `make_strategy(strategy_id)` factory — maps strategy IDs to `Strategy` instances |

## Container entry points

```python
from trading.app.container import build_container, build_worker_container

# Server process (includes HTTP API)
container = build_container()

# Worker process (no HTTP; Redis subscriber only)
container = build_worker_container()
```
