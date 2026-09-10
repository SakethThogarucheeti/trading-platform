# di

Global dependency injection layer. Uses [`dependency_injector`](https://python-dependency-injector.ets-labs.org/) — declarative, class-based containers wired together explicitly, not type-based resolution.

## How it works

`trading.di.containers.app.AppContainer` composes three sub-containers — `InfrastructureContainer` → `BrokerContainer` → `ComponentContainer` — passing each sub-container's outputs in as the next one's `providers.Dependency`/`providers.Container` inputs. There's a single process topology now (ingestor, candle aggregation, per-algo pipelines, dashboard, and the HTTP API all run in-process) — no separate worker-process container.

## Containers

| File | Class | What it provides |
|------|-------|-------------------|
| `containers/infra.py` | `InfrastructureContainer` | DB engine, session factory, every store, `Clock`, `PriceStore`, `ValueCache` (pure in-memory — no Redis backend), `CacherFactory` |
| `containers/broker.py` | `BrokerContainer` | `KiteClient`, `Broker` (real `ZerodhaBroker`, wrapped in `PaperBroker` when `settings.paper_trading`), `BrokerStream` |
| `containers/components.py` | `ComponentContainer` | Circuit breaker, tick/candle registries, `HistoricalDataService`, `HeartbeatMonitor`, the assembled `AbstractRuntime` (one `TickPipeline` per configured algo — see below), dashboard, scheduler |

`containers/components.py`'s `_RuntimeAssembler.build_runtime()` is where the per-algo fan-out happens: for each entry in `settings.algo_configs` it calls `AlgoPipelineFactory.build_and_wire()` (in `di/providers/algo_pipeline.py`) to build one `TickPipeline` and registers it on the shared `KiteIngestor`.

## Providers

`di/providers/` only holds the pieces that don't fit a `DeclarativeContainer`'s static-provider-graph shape — dynamic-count (one per algo) or plain helper construction:

| File | What it provides |
|------|-------------------|
| `algo_pipeline.py` | `AlgoPipelineFactory` — builds one algo's full pipeline (strategy via `trading_strategy_sdk.factory.create_strategy`, risk gates via `trading_risk_sdk.registry.create_gate`, `RiskFilter`, `OrderExecutor`, `FillHandler`, `PositionAccountant`, `SignalGenerator`) wired into one `TickPipeline` |
| `indicators.py` | `make_candle_store()` — builds a `CandleStore` (the `quantindicators`-facing `AbstractCandleStore` implementation) from a Postgres-backed candle store. Not currently called from any container — the live pipeline uses `quantindicators.polars_store.PolarsStore` directly instead (see `algo_pipeline.py`). Kept alive by its own unit test only; flagged separately as possibly-dead code. |

Strategy construction (`make_strategy`/`create_strategy`) and risk-gate construction now live in the extracted `trading-strategy-sdk`/`trading-risk-sdk` packages, not in this `di/` layer — `AlgoPipelineFactory` just calls into them.

## Container entry point

```python
from trading.di.containers.app import build_container

async with build_container() as container:
    runtime = await container.components.runtime()
```

There is no `build_worker_container()` — that was a leftover from a Kafka/worker-process split that was later removed in favor of the current single-process runtime.
