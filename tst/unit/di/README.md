# tst/unit/di/

Unit tests for `src/trading/di/`.

## Files

| File | What it tests |
|------|--------------|
| `test_container.py` | DI container resolution: `Settings`, `AsyncEngine`, `async_sessionmaker`, `TradingStore`, `AuditStore`, singleton scoping, and that a `dependency_injector` provider override (e.g. swapping in test-only settings) replaces what the container resolves |
| `test_indicators_provider.py` | `make_candle_store()`'s `CandleStore` construction |
| `test_algo_pipeline_build_and_wire.py` | `AlgoPipelineFactory.build_and_wire()` — seeds `algo_state`, wires the built `SignalGenerator` into the registry target, passes the circuit breaker/candle registry through, and returns the pipeline for the correct algo |
| `test_algo_pipeline_seed_state.py` | `AlgoPipelineFactory`'s state-seeding: writes blank state for a brand-new algo, doesn't clobber existing progress, always refreshes the config portion |
