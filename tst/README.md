# tst — Test suites

Three independent test workspaces, each with its own `pyproject.toml` and dependency set. Run them from their respective directories.

## Unit tests (`tst/unit/`)

Fast, no external services. Uses `aiosqlite` as an in-memory database substitute.

```bash
cd trading-platform
uv run pytest tst/unit/
```

Covers:
- All 57+ indicators against synthetic candle data
- Strategy signal logic (all four built-in strategies)
- `StrategyFactory` registration and instantiation
- Risk sizer (`calculate_quantity`)
- Registry behaviour (tick validation, candle aggregation, risk gates)
- Repository methods (candle, order, position, audit persistence)
- Report PnL computation (FIFO matching)
- Monitoring (heartbeat staleness detection)

## Strategy tests (`tst/integ/strategy-testing/`)

Requires Docker. Uses `testcontainers` to spin up an isolated Postgres schema per run.

```bash
cd trading-platform/tst/integ/strategy-testing
uv sync
uv run pytest strategy-testing/
```

Individual suites:

| File | What it tests |
|------|--------------|
| `test_backtest.py` | Full backtest run (all 4 strategies, Parquet replay) |
| `test_walk_forward.py` | Walk-forward analysis (in-sample / out-of-sample splits) |
| `test_monte_carlo.py` | Monte Carlo equity curve simulation |
| `test_hyperparam_search.py` | EMA crossover parameter grid search |
| `test_vwap_search.py` | VWAP reversion parameter grid search |
| `test_rsi_search.py` | RSI mean-reversion parameter grid search |
| `test_orb_search.py` | Opening range breakout parameter grid search |

## System / integration tests (`tst/integ/system-testing/`)

Requires Docker. Boots the full infrastructure (Postgres + Redis) and tests end-to-end scenarios.

```bash
cd trading-platform/tst/integ/system-testing
uv sync
uv run pytest system-testing/
```

Covers:
- Broker failure and circuit-breaker behaviour
- Full order lifecycle (PENDING → PLACED → FILLED)
- Risk guardrail enforcement (daily loss limit, duplicate position)
- State recovery after process restart

## Key testing conventions

- Unit tests never import `kiteconnect` or open network connections.
- `SimulatedClock` is injected in all tests so timestamp-sensitive logic is deterministic.
- Backtest and system tests use an isolated Postgres schema per run (no shared state between test runs).
- Strategy `on_candle()` tests always pass `timestamp=candle.timestamp` to `Signal` for reproducible signal IDs.
