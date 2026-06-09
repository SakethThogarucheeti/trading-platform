# trading

Root package for the algo-trading platform.

## Package map

```
trading/
├── app/            Composition root — container, pipeline, database, tasks
├── broker/         Broker abstraction + Zerodha and paper implementations
├── candles/        Tick → OHLCV bar aggregation and historical data
├── config/         Settings (env vars) and strategy config (JSON)
├── core/           Shared primitives — clock, lifecycle, messaging, models, schemas
├── di/             Global Dishka DI providers (wires all modules together)
├── execution/      Order placement, fill handling, position accounting
├── monitoring/     Heartbeat monitor and APScheduler wrapper
├── reports/        PnL and trade report generation
├── risk/           Signal validation, position sizing, risk gates
├── storage/        Shared infrastructure — Redis cache, CandleStore (indicator layer)
├── strategy/       Strategy ABC, built-in strategies, signal generator
├── tick_ingest/    WebSocket tick ingestion, circuit breaker, Redis publisher
├── worker/         Worker-process entry points (Redis subscriber, CB sync)
└── api/            FastAPI HTTP layer — routers, server component, Telegram alerter
```

## Module SDK layout

Every domain module follows the same four-layer structure:

```
<module>/
├── api/            Public contracts — __init__.py re-exports, interfaces.py, schemas.py
├── service/        Business logic (private; only imported by api/ and di/)
├── storage/        ORM models + concrete store classes
└── di/             Dishka provider(s) for this module
```

Other modules import **only** from `trading.<module>.api`. The `service/` and `storage/` layers are internal.

## Composition

`trading.app` is the sole place where modules are wired together:

| File | Role |
|------|------|
| `app/container.py` | Builds the Dishka `AsyncContainer` from all module providers |
| `app/pipeline.py`  | `TickPipeline` and `AlgoPipeline` — connects the registry chain |
| `app/database.py`  | Engine/session factory construction, `init_db` |
| `app/tasks.py`     | `fire()` — fire-and-forget background coroutine helper |

## Data flow

```
WebSocket tick
  → KiteIngestor (tick_ingest)
  → TickIngestor (circuit breaker, audit log)
  → TickPublisher → Redis pub/sub
  → TickSubscriber (worker process) → CandleAggregator
  → CandleAggregator (candles) → CandleEvent
  → SignalGenerator (strategy) → SignalEvent
  → RiskFilter (risk) → ValidatedOrderEvent
  → OrderExecutor (execution) → broker API call
  → FillHandler → position update
```

## Dependency graph

```
core              (clock, lifecycle, messaging — no domain deps)
broker            → core
tick_ingest       → core, broker.api
candles           → core, tick_ingest.api
strategy          → core, candles.api, quantindicators
risk              → core, strategy.api
execution         → core, risk.api
monitoring        → core
reports           → core
app               → ALL (sole composition point)
```
