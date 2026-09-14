# di/providers

Helper construction with a dynamic count (one per configured algo) or that's a plain factory
function rather than a fixed process-lifetime singleton `build_components()` can build directly.

| File | Provides | Notes |
|------|----------|-------|
| `algo_pipeline.py` | `AlgoPipelineFactory` (+ its `SharedAlgoDeps` dataclass of cross-algo dependencies) | `build_and_wire()` builds one algo's `TickPipeline`: strategy instance via `trading_strategy_sdk.factory.create_strategy`, risk gates via `trading_risk_sdk.registry.create_gate`, `RiskFilter`, `FillHandler` + `PositionAccountant` + `OrderExecutor`, `SignalGenerator`/`AlgoInstance`. Called once per entry in `settings.algo_configs` from `components.py`'s `_RuntimeAssembler.build_runtime()` — a `for` loop over runtime config, not something a fixed set of dataclass fields can express. |

The `containers/` package one level up holds the plain builder functions (`build_infra`,
`build_broker`, `build_components`, `build_ingestor_app`) and their dataclasses (`Infra`,
`BrokerDeps`, `Components`, `IngestorApp`) — see `di/README.md` for how they compose.
