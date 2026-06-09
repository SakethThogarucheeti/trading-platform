# config

Application configuration — environment variables and strategy parameters.

## Files

**`settings.py`** — `Settings` (Pydantic `BaseSettings`). Loaded once via `get_settings()` and cached. All env vars are read here; nothing else in the codebase reads `os.environ` directly.

Key settings: `postgres_url`, `redis_url`, `zerodha_api_key`, `zerodha_api_secret`, `token_secret_key`, `telegram_bot_token`, `telegram_chat_id`, `paper_trading` flag.

**`strategy_config.py`** — `StrategyConfig` loaded from `strategy_config.json`. Defines which algos run, their symbols, intervals, equity, and strategy-specific hyperparameters. Used by `di/providers/strategy.py` to build `AlgoInstance` maps at container startup.

## Usage

```python
from trading.config.settings import get_settings
settings = get_settings()
```
