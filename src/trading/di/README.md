# di

Dependency injection wiring using [Dishka](https://github.com/reagento/dishka). Assembles all components, registries, and infrastructure into a runnable container.

## Structure

```
di/
├── container.py
└── providers/
    ├── infra.py             # Settings, AsyncEngine, session factory, all domain stores, PriceStore, Redis
    ├── broker.py            # KiteClient, ZerodhaBroker (or PaperBroker), ZerodhaStream
    ├── components.py        # Main-process Runtime + all Component instances
    ├── worker_components.py # Worker-process Runtime (TickSubscriber instead of KiteIngestor)
    ├── indicators.py        # CandleStore with optional Redis cache
    └── strategy.py          # Strategy instances (dispatches to strategy/factory.py)
```

## Composition root

`container.py` assembles all providers into a single Dishka `AsyncContainer`. The container is the only place where concrete types (e.g. `ZerodhaBroker` vs `PaperBroker`) are chosen — all other code depends only on abstract interfaces.

```python
container = build_container(
    InfrastructureProvider(),
    BrokerProvider(),
    ComponentProvider(),
)
runtime = await container.get(Runtime)
await runtime.start()
```

## Provider responsibilities

### `InfrastructureProvider`
- `Settings` singleton (reads `.env`)
- `AsyncEngine` (Postgres connection pool)
- `async_sessionmaker` — shared across all stores
- All domain stores: `TradingStore`, `AuditStore`, `HeartbeatStore`, `CandleDataStore`, `ChartStore`, `InstrumentStore`, `ConfigStore`
- `PriceStore` (in-memory last-price cache for paper trading)
- Optional async Redis client

### `BrokerProvider`
- `KiteClient` (wraps `kiteconnect.KiteConnect`)
- `ZerodhaBroker` or `PaperBroker` depending on `settings.paper_trading`
- `ZerodhaStream`

### `ComponentProvider`
Builds the main-process component list and wires them into `Runtime`. Includes:
- `TickIngestor`, `KiteIngestor`, `TickPublisher` (tick ingestion)
- `CandleAggregator` (OHLCV aggregation)
- `HeartbeatMonitor` (liveness)
- `DashboardServer` (HTTP API — started last)
- `Scheduler` (market-hours cron)

### `WorkerComponentProvider`
Mirrors `ComponentProvider` for worker processes. Uses `TickSubscriber` (Redis) instead of `KiteIngestor` (WebSocket) and `RedisCircuitBreaker` instead of in-process `CircuitBreaker`. Activates only the named algo.

### `IndicatorsProvider` (indicators.py)
- `CandleStore` — Postgres-backed candle fetcher with optional Redis caching

### `StrategyProvider` (strategy.py)
- Strategy instances constructed from `strategy_config.json` via `strategy/factory.py`

## Why Dishka

Dishka supports async factories and async scoped lifetimes, matching the anyio-based component lifecycle. It also makes the composition root explicit — there is no global registry or service locator.
