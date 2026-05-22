# indicators/

Integration tests for the `quantindicators` indicator library. Each test pulls real OHLCV data from Postgres and runs indicators against it.

## Files

| File | What it tests |
|------|--------------|
| `test_indicator_smoke.py` | All ~70 indicators compute without error on 1 month of 15-min bars |
| `test_indicator_correlation.py` | Cross-indicator correlation analysis |
| `test_indicator_decay.py` | Indicator predictive power decay over holding periods |
| `test_indicator_ic.py` | Information Coefficient (IC) — signal quality metric |
| `test_indicator_ic_daily.py` | Daily IC aggregation |
| `test_indicator_quintile.py` | Quintile return analysis by indicator value |
| `test_indicator_regime.py` | Indicator behavior across trend/range/volatile regimes |
| `test_indicator_wf_ic.py` | Walk-forward IC (out-of-sample signal quality) |

The smoke test is the primary correctness check — if an indicator raises on valid OHLCV data, it fails here before it can fail in production.
