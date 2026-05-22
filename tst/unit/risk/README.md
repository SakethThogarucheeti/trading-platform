# tst/unit/risk/

Unit tests for `src/trading/risk/`.

## Files

| File | What it tests |
|------|--------------|
| `test_risk.py` | `calculate_quantity()` position sizing (equity %, stop distance, lot size rounding), `RiskFilter` decision logic (equity checks, daily loss limit, circuit breaker state, intraday cutoff time) |
| `test_controller.py` | `RiskController` re-exports: `RiskConfig`, `RiskRegistry` are accessible from the controller module |
