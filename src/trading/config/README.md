# config

Environment configuration and strategy parameter loading. All tuneable values live here; nothing else in the system reads `os.environ` or `.env` directly.

## Structure

```
config/
├── settings.py        # Pydantic BaseSettings — reads from .env / environment
└── strategy_config.py # Loads strategy_config.json (hyperparam grids + defaults)
```

## `Settings`

A `pydantic_settings.BaseSettings` singleton. All fields have safe defaults; only Zerodha credentials are required for live trading.

Key fields:

| Field | Default | Purpose |
|-------|---------|---------|
| `zerodha_api_key` | — | Kite Connect API key |
| `zerodha_api_secret` | — | Kite Connect API secret |
| `zerodha_access_token` | — | Daily access token (written by `scripts/login.py`) |
| `postgres_url` | `postgresql+asyncpg://…` | Async DB connection string |
| `paper_trading` | `False` | Swap live broker for `PaperBroker` |
| `max_daily_loss_pct` | `2.0` | Halt trading at this % daily drawdown |
| `risk_per_trade_pct` | `1.0` | Risk this % of equity per trade |
| `default_equity` | `10000` | Capital per algo when `ALGOS` is unset |
| `dashboard_enabled` | `True` | Enable FastAPI monitoring dashboard |
| `telegram_bot_token` | `""` | Optional Telegram alerting |

Obtain via `Settings.get_settings()` (cached singleton). Do not instantiate directly.

## `strategy_config.json`

Loaded by `strategy_config.py`. Contains:
- Default hyperparameters for each strategy (periods, multipliers, etc.)
- Parameter grids used by the strategy-testing hyperparam search scripts

The config is read-only at runtime; strategies receive their parameters through their constructor.
