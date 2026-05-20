# di

Dependency injection wiring using [Dishka](https://github.com/reagento/dishka). Assembles all components, registries, and infrastructure into a runnable container.

## Structure

```
di/
├── container.py
└── providers/
    ├── infra.py        # Singletons: Settings, AsyncEngine, Repository, PriceStore
    ├── broker.py       # KiteClient, ZerodhaBroker (or PaperBroker), ZerodhaStream
    ├── components.py   # Runtime, KiteIngestor, CandleAggregator, all AlgoRunners, etc.
    ├── indicators.py   # IndicatorContext per (symbol, interval)
    └── strategy.py     # Strategy instances per algo config entry
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
- `Repository` (single shared instance)
- `PriceStore` (in-memory last-price cache for paper trading)

### `BrokerProvider`
- `KiteClient` (wraps `kiteconnect.KiteConnect`)
- `ZerodhaBroker` or `PaperBroker` depending on `settings.paper_trading`
- `ZerodhaStream`

### `ComponentProvider`
- One `AlgoRunner` + `RiskController` + `OrderExecutor` per entry in the `ALGOS` config
- Shared: `KiteIngestor`, `CandleAggregator`, `HeartbeatMonitor`, `DashboardComponent`
- `Runtime` — receives the ordered list of all components

### `IndicatorsProvider`
- `IndicatorContext` instances bound to `(store, symbol, interval)` tuples

### `StrategyProvider`
- Strategy instances constructed from `strategy_config.json` parameters

## Why Dishka

Dishka supports async factories and async scoped lifetimes, matching the anyio-based component lifecycle. It also makes the composition root explicit — there is no global registry or service locator.
