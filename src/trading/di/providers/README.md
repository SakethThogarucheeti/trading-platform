# di/providers

Helper construction that doesn't fit a `dependency_injector` `DeclarativeContainer`'s static-provider-graph shape — either because the count is dynamic (one per configured algo) or because it's a plain factory function, not a class needing container scoping.

| File | Provides | Notes |
|------|----------|-------|
| `algo_pipeline.py` | `AlgoPipelineFactory` (+ its `SharedAlgoDeps` dataclass of cross-algo dependencies) | `build_and_wire()` builds one algo's `TickPipeline`: strategy instance via `trading_strategy_sdk.factory.create_strategy`, risk gates via `trading_risk_sdk.registry.create_gate`, `RiskFilter`, `FillHandler` + `PositionAccountant` + `OrderExecutor`, `SignalGenerator`/`AlgoInstance`. Called once per entry in `settings.algo_configs` from `ComponentContainer`'s `_RuntimeAssembler.build_runtime()` — a `for` loop over runtime config, not something a static container graph can express. |

The `containers/` package one level up holds the actual `DeclarativeContainer` subclasses (`AppContainer`, `InfrastructureContainer`, `BrokerContainer`, `ComponentContainer`) — see `di/README.md` for how they compose.
