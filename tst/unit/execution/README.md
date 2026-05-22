# tst/unit/execution/

Unit tests for `src/trading/execution/`.

## Files

| File | What it tests |
|------|--------------|
| `test_base.py` | `ExecutionEngine` ABC contract |
| `test_executor.py` | `OrderExecutor`: order placement via `MockBroker`, fill simulation, position DB update, idempotency (duplicate signal_id dropped), `PriceStore` integration |
| `test_order_executor_integ.py` | `OrderExecutor` against a real in-memory SQLite DB — verifies full PENDING→PLACED→FILLED state machine with concurrent fill scenarios |
