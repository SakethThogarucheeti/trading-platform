# core

Minimal shared primitives used across all modules. No domain logic, no DB access.

## Files

**`clock.py`** — `Clock` protocol + `SystemClock` (uses `datetime.now(UTC)`) and `SimulatedClock` (for backtests). Injected everywhere time-of-day decisions are made.

**`messaging.py`** — `AbstractRegistry` (async fan-out registry), `AbstractCircuitBreaker` (open/close interface), `FillObserver` (fill notification protocol).

**`schemas.py`** — Canonical Pydantic event models shared across modules:
- `TickEvent`, `CandleEvent`, `SignalEvent`, `ValidatedOrderEvent`, `FillEvent`
- Enums: `InstrumentType`, `Side`, `OrderType`, `OrderStatus`, `SignalType`

Module-level `api/schemas.py` files re-export from here — this is the single source of truth.

**`models.py`** — Legacy monolith ORM file. Still used transitionally for `AuditLog`, `DecisionLog`, and by `core/test_database.py`. Domain models have migrated to their owning module's `storage/models.py`.

**`context.py`** — Request context helpers.

**`types.py`** — Shared type aliases.

**`lifecycle/`** — `Component` ABC and `Runtime` supervisor. See [lifecycle/README.md](lifecycle/README.md).

## What moved out of core

App-level concerns that were in `core/` are now in `trading.app`:

| Old | New |
|-----|-----|
| `core/database.py` | `app/database.py` |
| `core/pipeline.py` | `app/pipeline.py` |
| `core/tasks.py` | `app/tasks.py` |
