# execution

Order lifecycle management: placement, fill simulation (paper), and idempotency. This package sits between `RiskRegistry` and the broker, ensuring that each validated signal results in exactly one order regardless of retries or process restarts.

## Structure

```
execution/
├── base.py            # ExecutionEngine ABC
├── order_executor.py  # OrderExecutor — handles ValidatedOrderEvent → broker → DB
└── idempotency.py     # is_duplicate() — DB-backed duplicate detection
```

## `ExecutionEngine` ABC

```python
class ExecutionEngine(ABC):
    @abstractmethod
    async def execute(self, order: ValidatedOrderEvent, session: AsyncSession) -> None: ...
```

`OrderExecutor` implements the full execution logic for both live and paper trading.

## `OrderExecutor`

Handles `ValidatedOrderEvent` objects. Checks idempotency, persists the order, calls the broker, and updates position. Event-driven (called synchronously from the pipeline), not a polling loop.

## Idempotency (`idempotency.py`)

```python
async def is_duplicate(signal_id: UUID, session: AsyncSession) -> bool
```

Checks whether an `Order` row already exists for the given `signal_id`. Called at the start of every `ExecRegistry.handle()` call. If `True`, the event is silently dropped — preventing double-fills if a tick is processed twice (e.g. after a process restart that replays the last tick).

## Order lifecycle

```
ValidatedOrderEvent received
    │
    ├─ is_duplicate? → yes → drop silently
    │
    ▼ no
    │
    ├─ INSERT orders (status=PENDING)   ← crash-safe: order tracked before broker call
    │
    ├─ broker.place_order()             ← async REST call outside transaction
    │
    ├─ UPDATE orders (status=PLACED, kite_order_id=…)
    │
    └─ Paper trading? ──yes──► simulate fill from PriceStore
                     ──no───► wait for postback webhook (Kite order-update callback)
                                 └─► UPDATE orders (status=FILLED, avg_price=…)
                                     UPDATE positions (atomic SELECT … FOR UPDATE)
```

Position updates use `SELECT … FOR UPDATE` to prevent concurrent fills for the same symbol from producing an inconsistent position.
