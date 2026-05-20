# scripts

CLI utilities for day-to-day operation: authentication, data management, and reporting. None of these run as part of the live trading process.

## Scripts

### `login.py` — Daily Zerodha token refresh

```bash
uv run python -m trading.scripts.login
```

Zerodha access tokens expire at midnight. Run this each morning before market open. The script:
1. Opens a browser to the Kite login page.
2. Starts a local HTTP server on port 8080 to capture the redirect.
3. Exchanges the request token for an access token.
4. Writes `ZERODHA_ACCESS_TOKEN=<token>` into `.env`.

> The Kite developer app's redirect URL must be set to `http://127.0.0.1:8080/`.

### `fetch_data.py` — Historical OHLCV download

```bash
uv run python -m trading.scripts.fetch_data --symbol INFY --interval 15minute --days 90
```

Downloads historical candles from Zerodha and saves to Parquet files. Used to build datasets for backtesting and strategy grid searches.

### `import_candles.py` — Bulk candle import to Postgres

```bash
uv run python -m trading.scripts.import_candles --file data/INFY_15min.parquet
```

Imports Parquet candle files into the `candles` table. Idempotent — uses `ON CONFLICT DO NOTHING`.

### `day_end_report.py` / `week_report.py` / `month_report.py` — P&L reports

```bash
uv run python -m trading.scripts.day_end_report
```

Queries the database, computes FIFO P&L, and prints a trade summary. Sends to Telegram if configured.

### `test_login.py` / `test_zerodha.py` — Manual integration checks

One-off scripts to verify Zerodha connectivity. Not part of the automated test suite.
