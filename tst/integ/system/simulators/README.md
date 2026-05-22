# simulators/

Test utilities for system integration tests.

## Files

| File | Purpose |
|------|---------|
| `fault_injector.py` | `FaultInjector` — wraps a broker to inject configurable errors |

## FaultInjector

Wraps any `Broker` implementation and selectively raises exceptions or returns error responses to simulate real-world failure modes:

- Broker timeout / HTTP 5xx on `place_order()`
- Partial fills
- WebSocket disconnection

Used by `test_broker_failure.py` and `test_state_recovery.py` to verify the pipeline handles faults gracefully without data corruption or duplicate orders.
