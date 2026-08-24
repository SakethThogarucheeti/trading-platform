# tst/unit/execution/

Unit tests for `src/trading/execution/`.

## Files

| File | What it tests |
|------|--------------|
| `test_base.py` | `ExecutionEngine` ABC contract |
| `test_executor.py` | `OrderExecutor`: order placement via `MockBroker`, fill simulation, position DB update, idempotency (duplicate signal_id dropped), `PriceStore` integration |
