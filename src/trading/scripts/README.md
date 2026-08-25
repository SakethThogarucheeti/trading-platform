# scripts

Three of these are registered `pyproject.toml` `[project.scripts]` entries, invoked via
`uv run <command>`. `login.py` has its own `main()`/`if __name__ == "__main__"` but isn't
registered as a project script — invoke it as a module. `test_login.py`/`test_zerodha.py` are
interactive manual scripts with no `main()` at all — run them directly by file path.

| Command | File | Invocation | What it does |
|---------|------|------------|-------------|
| `fetch-data` | `fetch_data.py` | `uv run fetch-data` | Downloads historical OHLCV candles from Zerodha and saves them as Parquet files under `data/<symbol>/<interval>.parquet` |
| `import-candles` | `import_candles.py` | `uv run import-candles` | Bulk-inserts Parquet candle files into the `candles` Postgres table; safe to re-run (conflicts are ignored) |
| `report` | `report.py` | `uv run report` | Prints a PnL/trade report to stdout using `ReportEngine` |
| — | `login.py` | `uv run python -m trading.scripts.login` | OAuth handshake with Zerodha — exchanges the request token for an access token and writes it as plaintext to `ZERODHA_ACCESS_TOKEN` in `.env` |
| — | `test_login.py` | `uv run python src/trading/scripts/test_login.py` | Standalone manual login smoke test — prompts for a request token, exchanges it for a fresh access token, and prints the profile; doesn't read or write `.env` |
| — | `test_zerodha.py` | `uv run python src/trading/scripts/test_zerodha.py` | End-to-end connectivity check (positions, instruments fetch) |

`login.py`'s `.env`-based token is a separate path from the live API's own login flow
(`api/routers/auth.py`'s `auth_callback`), which stores the token encrypted in the
`broker_tokens` Postgres table instead — the two paths have different storage semantics.
