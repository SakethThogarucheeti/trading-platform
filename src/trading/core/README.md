# core

Shared domain primitives: ORM models, Pydantic event schemas, database infrastructure, and the pipeline base class. Everything else in the system imports from here; this package imports from nothing else in `trading/`.

## Structure

```
core/
├── models.py     # SQLAlchemy ORM — the persistent record of every pipeline event
├── schemas.py    # Pydantic event DTOs — the in-memory flow between registries
├── messaging.py  # AbstractRegistry — base class for all pipeline stages
├── database.py   # Async engine factory + session context manager
├── clock.py      # Clock ABC, SystemClock, SimulatedClock
└── tasks.py      # fire() — fire-and-forget async background task helper
```

## ORM models (`models.py`)

| Model | Table | Written by | Purpose |
|-------|-------|------------|---------|
| `Candle` | `candles` | `CandleRegistry` | OHLCV bars; unique on `(symbol, interval, timestamp)` |
| `Signal` | `signals` | `RiskRegistry` | Accepted signals with strategy and stop-distance |
| `Order` | `orders` | `ExecRegistry` | Full order lifecycle: PENDING → PLACED → FILLED |
| `Position` | `positions` | `ExecRegistry` | Net quantity + weighted average price per `(symbol, instrument_type)` |
| `TickLog` | `tick_logs` | `TickRegistry` | Immutable append-only record of every raw tick |
| `DecisionLog` | `decision_logs` | all registries | Pipeline audit trail; `tick_log_id` foreign key enables full causal reconstruction |
| `AuditLog` | `audit_logs` | `RiskRegistry`, `ExecRegistry` | Free-form operational events |

## Event schemas (`schemas.py`)

Each schema carries `tick_log_id` from the originating tick all the way through to execution, enabling a single `WHERE tick_log_id = X` query to reconstruct the full causal chain.

```
dict (raw Kite tick)
  → TickEvent
      → CandleEvent
          → SignalEvent
              → ValidatedOrderEvent
```

## `AbstractRegistry`

The single method every pipeline stage must implement:

```python
class AbstractRegistry(ABC):
    @abstractmethod
    async def handle(self, event: Any) -> Any:
        ...
```

Concrete registries add their own config `@dataclass` and internal state; the only coupling between stages is the event schema types.

## Clock abstraction

`SimulatedClock` is injected during backtests so that all timestamp calculations (`datetime.now()`) use bar-close time instead of wall-clock time. All components accept a `Clock` at construction — never call `datetime.now()` directly.

```python
class Clock(ABC):
    def now(self) -> datetime: ...

class SystemClock(Clock): ...       # wall time — used in live trading
class SimulatedClock(Clock): ...    # advances per bar — used in backtests
```
