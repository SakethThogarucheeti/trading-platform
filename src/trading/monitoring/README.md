# monitoring

Liveness monitoring and scheduled background tasks.

## Layout

```
monitoring/
├── api/
│   ├── __init__.py       Re-exports: HeartbeatMonitor, Scheduler, AbstractHeartbeatStore
│   └── interfaces.py     AbstractHeartbeatStore protocol
├── service/
│   ├── heartbeat.py      HeartbeatMonitor — periodic DB heartbeat + stale component alerting
│   └── scheduler.py      Scheduler — APScheduler wrapper (runs market-hours jobs)
├── storage/
│   ├── models.py         Heartbeat ORM model
│   └── store.py          HeartbeatStore
└── di/
    └── providers.py      MonitoringProvider
```

## Key concepts

**`HeartbeatMonitor`** is a `Component`. On each beat interval it writes a timestamp to the `heartbeats` table for every registered component name. It also queries for stale entries (last_seen older than `timeout_secs`) and, if any are found, calls its `alerter` — a plain `Callable[[str], Awaitable[None]]` passed in at construction — rather than a dedicated Protocol type.

**`Scheduler`** wraps APScheduler. It registers jobs (e.g. daily PnL reset, end-of-day position close) and starts/stops with the component lifecycle.

`api/telegram.py` (in the `trading.api` package) provides `TelegramAlerter`, wrapped in a small closure and passed as that `alerter` callable.

## Imports

```python
from trading.monitoring.api import HeartbeatMonitor, Scheduler
```
