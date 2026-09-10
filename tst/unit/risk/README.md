# tst/unit/risk/

Unit tests for `src/trading/risk/`.

## Files

| File | What it tests |
|------|--------------|
| `test_risk.py` | `calculate_quantity()` position sizing (equity %, stop distance, lot size rounding), `RiskFilter` decision logic (equity checks, daily loss limit, circuit breaker state, intraday cutoff time) |
| `test_controller.py` | `risk/service/filter.py` and `risk/api` exports: `RiskConfig` is constructible, `RiskFilter` is accessible from both the service module and the module's public `api` |
