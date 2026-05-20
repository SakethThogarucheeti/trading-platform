# engine

Async runtime, component lifecycle, and market-hours scheduling. This package turns the stateless registry pipeline into a running process.

## Structure

```
engine/
├── component.py          # Component ABC — CREATED → RUNNING → STOPPED lifecycle
├── runtime.py            # Runtime — ordered startup + reverse-order shutdown
├── scheduler.py          # APScheduler — fires Runtime.start/stop at market open/close
├── kite_ingestor.py      # KiteIngestor — Zerodha WebSocket → TickRegistry
├── candle_aggregator.py  # CandleAggregator — warmup fetch on startup, then idle
├── algo_runner.py        # AlgoRunner — lifecycle wrapper around AlgoRegistry
└── heartbeat.py          # HeartbeatMonitor — liveness checks + Telegram alerts
```

## `Component` lifecycle

All long-running services inherit `Component`. The three hooks map to distinct phases:

```
CREATED
  │  _setup()   — connect WebSocket, fetch warm-up data, acquire resources
  ▼
RUNNING
  │  _run()     — await sleep_forever() (event-driven) or explicit poll loop
  ▼
STOPPING
  │  _teardown() — close connections, flush buffers
  ▼
STOPPED
```

`tg.start(component.start)` blocks until `_setup()` completes, so `Runtime` can guarantee component A is ready before starting component B.

## `Runtime` — ordered startup

Components start in declaration order; each one's `_setup()` must complete before the next begins:

```
KiteIngestor        → WebSocket connected, circuit breaker armed
CandleAggregator    → historical warm-up candles fetched into PolarsStore
AlgoRunner(s)       → strategies ready for first candle
RiskController      → risk checks armed
OrderExecutor       → broker connection verified
HeartbeatMonitor    → liveness polling started
DashboardComponent  → HTTP server listening
```

Shutdown is reverse order. Calling `runtime.stop()` (e.g. at `Ctrl+C` or the 15:30 scheduler job) drains in-flight work before tearing down infrastructure.

## `Scheduler`

Wraps APScheduler. Two jobs fire every weekday:

| Job | Time (IST) | Action |
|-----|------------|--------|
| Market open | 09:15 | `runtime.start()` |
| Market close | 15:30 | `runtime.stop()` |

If the process starts during market hours, `Runtime.start()` is called immediately on boot without waiting for the next schedule.

## `KiteIngestor`

Bridges the Zerodha WebSocket (which fires callbacks on a background thread) to the async event loop. For each incoming tick batch:

1. Schedules `_handle_tick(raw)` on the event loop via `loop.call_soon_threadsafe`.
2. `_handle_tick` calls `TickRegistry.handle(raw)`.
3. Updates `PriceStore` with the latest price (used by paper trading for fill simulation).
4. Resets the circuit-breaker timer on each batch.

## `HeartbeatMonitor`

Writes its own heartbeat to Postgres every N seconds. On each tick it reads all heartbeat rows and fires a Telegram alert for any module that has gone stale. This provides out-of-band liveness detection — if the process crashes mid-session the alert fires within one heartbeat interval.
