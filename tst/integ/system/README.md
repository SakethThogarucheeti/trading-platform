# tst/integ/system/ — System integration tests

End-to-end pipeline tests against real Postgres + Redis (via Docker / testcontainers).

```bash
cd tst/integ/system
uv sync
uv run pytest . -x -q
```

## What is tested

| File | Scenario |
|------|---------|
| `test_integration.py` | Full pipeline: `SignalGenerator` → `RiskFilter` → `OrderExecutor` with synthetic candles |
| `test_order_lifecycle.py` | Order state machine: PENDING → PLACED → FILLED, position tracking |
| `test_risk_guardrails.py` | Risk controller enforcement: daily loss, position limits, intraday cutoff |
| `test_broker_failure.py` | Circuit breaker behaviour on broker errors; retry logic |
| `test_state_recovery.py` | Component state persistence and recovery after process failure |

## Simulators

`simulators/fault_injector.py` — `FaultInjector` wraps a broker and injects configurable errors (timeouts, HTTP 5xx, partial fills) to test resilience paths.

## Fixtures

`conftest.py` provides:
- `pg_container` (session-scoped) — real Postgres 16-alpine container
- `engine` (per-test) — async SQLAlchemy engine; tables created on first use, all rows truncated after each test
- `session_factory` (per-test) — async session factory bound to the test engine
