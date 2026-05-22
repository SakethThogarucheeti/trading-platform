# di/providers/

Dishka DI provider modules. Each file groups related providers by concern.

## Files

| File | Scope | Provides |
|------|-------|---------|
| `broker.py` | APP | `KiteClient`, `ZerodhaBroker` or `PaperBroker`, `ZerodhaStream` |
| `components.py` | APP | All main-process `Component` instances and `Runtime` |
| `infra.py` | APP | `Settings`, `AsyncEngine`, `async_sessionmaker`, all domain stores, `PriceStore`, Redis client |
| `indicators.py` | APP | `CandleStore` (Postgres-backed, optional Redis cache) |
| `strategy.py` | APP | Strategy instances (dispatches to `strategy/factory.py` by name) |
| `worker_components.py` | APP | Worker-process `Component` instances and `Runtime` |

## Key providers

**`InfrastructureProvider`** (`infra.py`) is the foundation — it creates the DB engine and session factory that everything else depends on. It also provides all domain store instances: `TradingStore`, `AuditStore`, `HeartbeatStore`, `CandleDataStore`, `ChartStore`, `InstrumentStore`, `ConfigStore`.

**`ComponentProvider`** (`components.py`) is the main-process orchestration point. It reads instrument tokens from DB, builds `TickConfig` and `CandleConfig`, instantiates all `Component` subclasses, wires the scheduler callbacks, and hands the ordered list to `Runtime`. Change component startup order here.

**`WorkerComponentProvider`** (`worker_components.py`) mirrors `ComponentProvider` for worker processes. It uses `TickSubscriber` instead of `KiteIngestor` and `RedisCircuitBreaker` instead of the in-process `CircuitBreaker`. Activates only the named algo.

**`BrokerProvider`** (`broker.py`) is isolated so a `MockBrokerProvider` can replace it entirely in tests without touching other providers.

## Adding a new provider

1. Create a new Dishka `Provider` subclass in a new file here.
2. Register it in `di/container.py` under `build_container()` or `build_worker_container()`.
