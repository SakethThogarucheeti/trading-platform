# tst/unit/core/

Unit tests for `src/trading/core/`.

## Files

| File | What it tests |
|------|--------------|
| `test_clock.py` | `SystemClock` (real time, timezone), `SimulatedClock` (manual advancement) |
| `test_database.py` | SQLAlchemy ORM init, model constraints, relationships |
| `test_messaging.py` | `AbstractRegistry` pattern, `CircuitBreaker` fault injection |
| `test_pipeline.py` | `AlgoPipeline` (signal → risk → executor), `TickPipeline` (tick → candle → signal → algo) |
| `test_schemas.py` | Pydantic event schema validation: `TickEvent`, `CandleEvent`, `SignalEvent`, `OrderEvent` |
| `test_tasks.py` | `fire()` fire-and-forget async background task helper |

## Sub-directories

| Directory | Tests for |
|-----------|---------|
| `lifecycle/` | `Component` ABC, `Runtime` supervisor |
