# di/providers

One file per provider group. Each file contains one or more Dishka `Provider` subclasses.

| File | Provider class(es) | Scope |
|------|--------------------|-------|
| `infra.py` | `InfrastructureProvider` | APP — engine, session factory, all stores |
| `broker.py` | `BrokerProvider`, `RedisProvider` | APP — `Broker`, `BrokerStream`, Redis client |
| `components.py` | `ComponentProvider` | APP — ingestor, aggregator, signal generators, heartbeat |
| `algo_pipeline.py` | `AlgoPipelineProvider` | APP — `RiskFilter` + `OrderExecutor` per algo, `AlgoPipeline` |
| `worker_components.py` | `WorkerComponentProvider` | APP — worker-process component set |
| `indicators.py` | `make_candle_store()` factory function | called by `InfrastructureProvider` |
| `strategy.py` | `make_strategy()` factory function | called during algo instance construction |

All providers use `Scope.APP` — dependencies are singletons for the lifetime of the container. There is no request scope.
