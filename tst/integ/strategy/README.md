# tst/integ/strategy/ — Strategy integration tests

Backtesting, indicator validation, walk-forward analysis, and hyperparameter search. Requires Docker (Postgres via testcontainers).

```bash
cd tst/integ/strategy
uv sync
uv run pytest . -x -q
```

## Layout

| Directory | Contents |
|-----------|---------|
| `indicators/` | Smoke tests for all ~70 indicators |
| `strategies/` | Backtest, walk-forward, Monte Carlo, parameter search tests |
| `testing/` | Shared backtesting harness and utilities (the `testing` library) |

## Fixtures

`conftest.py` provides:
- `pg_container` (session-scoped) — real Postgres 16-alpine container
- `pg_engine` (per-test) — async engine; tables created on first use, all rows truncated after each test

The `testing/` library (importable as `import testing`) is added to `sys.path` by `conftest.py`.
