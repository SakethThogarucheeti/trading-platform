# monitoring/

Market-hours scheduling and liveness alerting. Add new health-check or alerting integrations here.

## Files

| File | Purpose |
|------|---------|
| `scheduler.py` | APScheduler cron wrapper for market-hours events |
| `heartbeat.py` | DB liveness beats + Telegram staleness alerts |

## Scheduler

`Scheduler` wraps APScheduler's `AsyncIOScheduler` with IST timezone. The following jobs are registered at startup by the DI container:

| Job | Time (IST) | Trigger |
|-----|-----------|---------|
| Market open | 09:15 Mon–Fri | `Runtime.start()` |
| Market close | 15:30 Mon–Fri | `Runtime.stop()` |
| EOD report | 15:45 Mon–Fri | report generation |
| Weekly instrument sync | Sun 10:00 | symbol master refresh |
| Position reset (optional) | 15:29 Mon–Fri | clear intraday positions |

## HeartbeatMonitor

`HeartbeatMonitor` is a `Component` that runs two concurrent inner loops:

- **`_beat_loop`** — upserts the module's own `last_seen` timestamp in Postgres every N seconds.
- **`_monitor_loop`** — queries `HeartbeatStore.get_stale_modules()` and invokes the alerter callback (e.g., Telegram message) for any module that has gone silent beyond the configured timeout.

The Telegram alerter is rate-limited to avoid flooding when a module is persistently stale.

## Relationship to other packages

- `storage/stores/heartbeat.py` — persistence layer for heartbeat rows
- `api/telegram.py` — Telegram alerter passed to `HeartbeatMonitor`
- `di/providers/components.py` — wires `HeartbeatMonitor` into the `Runtime`
- `main.py` — `Scheduler` is resolved from the DI container and started before `sleep_forever()`
