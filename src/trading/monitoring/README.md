# monitoring

Liveness monitoring and scheduled background tasks.

## Layout

```
monitoring/
├── api/
│   ├── __init__.py       Re-exports: HeartbeatMonitor, Scheduler, AbstractHeartbeatStore,
│   │                                 AbstractAlerter
│   └── interfaces.py     AbstractHeartbeatStore, AbstractAlerter protocols
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

**`HeartbeatMonitor`** is a `Component`. On each beat interval it writes a timestamp to the `heartbeats` table for every registered component name. It also queries for stale entries (last_seen older than `timeout_secs`) and fires an alert via `AbstractAlerter` if any are found.

**`Scheduler`** wraps APScheduler. It registers jobs (e.g. daily PnL reset, end-of-day position close) and starts/stops with the component lifecycle.

`api/telegram.py` (in the `trading.api` package) provides the `TelegramAlerter` that implements `AbstractAlerter`.

## Imports

```python
from trading.monitoring.api import HeartbeatMonitor, Scheduler
```
