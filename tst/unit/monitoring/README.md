# tst/unit/monitoring/

Unit tests for `src/trading/monitoring/`.

## Files

| File | What it tests |
|------|--------------|
| `test_scheduler.py` | `Scheduler` job registration (market_open, market_close, eod_report, instrument_sync), start/stop lifecycle, job fire times in IST |
| `test_heartbeat.py` | `HeartbeatMonitor` stale detection, `TelegramAlerter` API calls, rate limiting on repeated staleness alerts |
