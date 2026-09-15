# tst/unit/reports/

Unit tests for `src/trading/reports/`.

## Files

| File | What it tests |
|------|--------------|
| `test_engine.py` | `fetch_report_data()` funnel counts (signal_generated, signal_accepted, signal_rejected, order placed/filled), P&L calculation, benchmark comparison |
| `test_reports.py` | `fetch_signals()`, `fetch_decisions()`, `fetch_audit_logs()`, `fetch_heartbeats()`; `compute_pnl()` FIFO matching for simple buy/sell scenarios; report rendering utilities |
| `test_trades.py` | `fetch_filled_trades()` FIFO-matched realized `gross` per trade (not signed cash flow, trading-platform#89): opening vs. closing fills, cross-window FIFO seeding from the local trading day, the daily-flat-position day boundary, and `algo_name` filtering |
