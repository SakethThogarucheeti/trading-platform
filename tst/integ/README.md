# tst/integ/ — Integration tests

Two suites, each with its own Python environment and `pyproject.toml`. Both require Docker to spin up Postgres (and Redis for system tests).

## Suites

| Directory | What it tests | Run from |
|-----------|--------------|---------|
| `strategy/` | Indicators, backtests, walk-forward, Monte Carlo, hyperparameter search | `tst/integ/strategy/` |
| `system/` | Full end-to-end pipeline: tick → signal → order → position | `tst/integ/system/` |

## Quick start

```bash
# Strategy tests
cd tst/integ/strategy && uv sync && uv run pytest .

# System tests
cd tst/integ/system && uv sync && uv run pytest .
```

## Conventions

- Each suite has a single `conftest.py` that provides `pg_container` (session-scoped Postgres via testcontainers) and per-test fixtures.
- All tables are truncated after each test — tests are isolated and can run in any order.
- Tests in `strategy/` use the `testing/` library for backtesting harness, Monte Carlo engine, and walk-forward splits.
