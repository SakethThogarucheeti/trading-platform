# scripts

CLI entry points. All invoked via `uv run <command>` as defined in `pyproject.toml`.

| Command | File | What it does |
|---------|------|-------------|
| `login` | `login.py` | OAuth handshake with Zerodha — exchanges the request token for an access token and stores it encrypted in the DB |
| `fetch-data` | `fetch_data.py` | Downloads historical OHLCV candles from Zerodha and saves them as Parquet files under `data/<symbol>/<interval>.parquet` |
| `import-candles` | `import_candles.py` | Bulk-inserts Parquet candle files into the `candles` Postgres table; safe to re-run (conflicts are ignored) |
| `report` | `report.py` | Prints a PnL/trade report to stdout using `ReportEngine` |
| `test-login` | `test_login.py` | Smoke-tests the stored access token against the Zerodha API |
| `test-zerodha` | `test_zerodha.py` | End-to-end connectivity check (positions, instruments fetch) |
