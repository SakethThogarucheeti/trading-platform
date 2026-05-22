# tst/unit/config/

Unit tests for `src/trading/config/`.

## Files

| File | What it tests |
|------|--------------|
| `test_settings.py` | `Settings` pydantic validation (required fields, percentage constraints, interval validation, heartbeat timeout defaults), `get_settings()` caching, Telegram enablement logic |
| `test_strategy_config.py` | `StrategyConfig` JSON loading, hyperparam search grid accessors, parameter parsing, file-not-found error |
