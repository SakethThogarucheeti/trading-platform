# tst — Test suites

Fast unit tests run from the project root. Integration tests have moved to the
sibling repo `../trading-integ-tests/` (each suite keeps its own `pyproject.toml`).

## Unit tests (`tst/unit/`)

Fast, no external services.

```bash
cd trading-platform
uv run pytest tst/unit/
```

The unit test layout mirrors `src/trading/` — each package has a corresponding directory under `tst/unit/`. See `tst/unit/README.md` for details.

## Integration tests (`../trading-integ-tests/`)

Requires Docker. Two suites, each with its own Python environment:

```bash
# Strategy: indicator smoke tests, backtests, walk-forward, Monte Carlo, hyperparameter search
cd ../trading-integ-tests/strategy
uv sync
uv run pytest .

# System: end-to-end pipeline — broker failure, order lifecycle, risk guardrails, state recovery
cd ../trading-integ-tests/system
uv sync
uv run pytest .
```

Both depend on `trading-platform` via an editable path dependency (`../../trading-platform`). See `trading-integ-tests/strategy/README.md` and `trading-integ-tests/system/README.md` for details.

## Key testing conventions

- Unit tests never import `kiteconnect` or open network connections.
- `SimulatedClock` is injected in all tests so timestamp-sensitive logic is deterministic.
- Integration tests use a real Postgres container; each test truncates all tables in teardown.
- Strategy `on_candle()` tests always pass `timestamp=candle.timestamp` to `Signal` for reproducible signal IDs.
