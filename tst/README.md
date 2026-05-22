# tst — Test suites

Two test workspaces: fast unit tests run from the project root; integration tests each have their own `pyproject.toml`.

## Unit tests (`tst/unit/`)

Fast, no external services.

```bash
cd trading-platform
uv run pytest tst/unit/
```

The unit test layout mirrors `src/trading/` — each package has a corresponding directory under `tst/unit/`. See `tst/unit/README.md` for details.

## Strategy integration tests (`tst/integ/strategy/`)

Requires Docker. Uses `testcontainers` to spin up an isolated Postgres schema per run.

```bash
cd trading-platform/tst/integ/strategy
uv sync
uv run pytest .
```

Covers indicator smoke tests, full backtests, walk-forward analysis, Monte Carlo simulation, and hyperparameter search. See `tst/integ/strategy/README.md` for details.

## System integration tests (`tst/integ/system/`)

Requires Docker. Boots Postgres + Redis and tests end-to-end pipeline scenarios.

```bash
cd trading-platform/tst/integ/system
uv sync
uv run pytest .
```

Covers broker failure, order lifecycle, risk guardrails, and state recovery. See `tst/integ/system/README.md` for details.

## Key testing conventions

- Unit tests never import `kiteconnect` or open network connections.
- `SimulatedClock` is injected in all tests so timestamp-sensitive logic is deterministic.
- Integration tests use a real Postgres container; each test truncates all tables in teardown.
- Strategy `on_candle()` tests always pass `timestamp=candle.timestamp` to `Signal` for reproducible signal IDs.
