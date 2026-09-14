# di

Global dependency-wiring layer. Plain builder functions returning plain dataclasses — no DI
framework (see "History" below).

## How it works

`trading.di.containers.app.build_ingestor_app()` calls `build_infra()` → `build_broker()` →
`build_components()` in order, threading each stage's output into the next as an ordinary
function argument, and yields an `IngestorApp` dataclass bundling all three. There's a single
process topology (ingestor, candle aggregation, per-algo pipelines, dashboard, and the HTTP API
all run in-process) — no separate worker-process entry point.

## Builders

| File | Function / dataclass | What it provides |
|------|-----------------------|-------------------|
| `containers/infra.py` | `build_infra()` → `Infra` | DB engine, session factory, every store, `Clock`, `PriceStore`, `ValueCache` (pure in-memory — no Redis backend), `CacherFactory` |
| `containers/broker.py` | `build_broker()` → `BrokerDeps` | `KiteClient`, `Broker` (real `ZerodhaBroker`, wrapped in `PaperBroker` when `settings.paper_trading`), `BrokerStream` |
| `containers/components.py` | `build_components()` → `Components` | Circuit breaker, tick/candle registries, `HistoricalDataService`, `HeartbeatMonitor`, the assembled `AbstractRuntime` (one `TickPipeline` per configured algo — see below), dashboard, scheduler |

`containers/components.py`'s `_RuntimeAssembler.build_runtime()` is where the per-algo fan-out happens: for each entry in `settings.algo_configs` it calls `AlgoPipelineFactory.build_and_wire()` (in `di/providers/algo_pipeline.py`) to build one `TickPipeline` and registers it on the shared `KiteIngestor`.

Construction is eager throughout — each builder function constructs everything it returns before
returning, no lazy/on-first-access semantics. This matches what the old container-based version
actually did at runtime in practice (`main.py` resolved every provider unconditionally at boot),
so this isn't a behavior change, just a code-shape one (see "History" below).

## Providers

`di/providers/` only holds the pieces with a dynamic count (one per algo) or that are plain
helper construction rather than a fixed set of process-lifetime singletons:

| File | What it provides |
|------|-------------------|
| `algo_pipeline.py` | `AlgoPipelineFactory` — builds one algo's full pipeline (strategy via `trading_strategy_sdk.factory.create_strategy`, risk gates via `trading_risk_sdk.registry.create_gate`, `RiskFilter`, `OrderExecutor`, `FillHandler`, `PositionAccountant`, `SignalGenerator`) wired into one `TickPipeline` |
| `indicators.py` | `make_candle_store()` — builds a `CandleStore` (the `quantindicators`-facing `AbstractCandleStore` implementation) from a Postgres-backed candle store. Not currently called from `build_components()` — the live pipeline uses `quantindicators.polars_store.PolarsStore` directly instead (see `algo_pipeline.py`). Kept alive by its own unit test only; flagged separately as possibly-dead code. |

Strategy construction (`make_strategy`/`create_strategy`) and risk-gate construction now live in the extracted `trading-strategy-sdk`/`trading-risk-sdk` packages, not in this `di/` layer — `AlgoPipelineFactory` just calls into them.

## Entry point

```python
from trading.di.containers.app import build_ingestor_app

async with build_ingestor_app() as app:
    runtime = app.components.runtime
```

Tests that need to swap in fake infra (an in-memory sqlite engine instead of a live Postgres
connection) call `build_infra(fake_settings, engine=fake_engine)` directly — see
`tst/unit/di/test_container.py`.

There is no separate worker-process entry point — that was a leftover from a Kafka/worker-process
split that was later removed in favor of the current single-process runtime.

## History

This layer used to be four `dependency_injector.DeclarativeContainer` classes (`AppContainer`,
`InfrastructureContainer`, `BrokerContainer`, `ComponentContainer`). Collapsed to plain builder
functions + dataclasses (trading-platform#3) once it became clear the framework wasn't earning
its weight: exactly one production call site (`main.py`), exactly one test file using
`.override()`, and nothing anywhere relying on the framework's nominal lazy-singleton semantics
(main.py resolved every provider unconditionally at boot regardless). `dependency-injector` is no
longer a dependency of this package.
