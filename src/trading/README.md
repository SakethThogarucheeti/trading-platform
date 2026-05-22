# trading/

The full algo-trading platform. This document explains the architecture end-to-end — what each package does, how data flows through the system, and how the two process types (ingestor and worker) relate to each other.

---

## Package map

| Package | One-line mandate |
|---------|-----------------|
| `core/` | Domain primitives shared by everything: ORM models, Pydantic event schemas, clock, database, pipeline wiring |
| `core/lifecycle/` | Async `Component` base class and `Runtime` supervisor |
| `config/` | `Settings` (env/`.env`) and strategy parameter loading |
| `broker/` | Abstract `Broker` + `BrokerStream` interfaces |
| `broker/base/` | Abstract interfaces |
| `broker/zerodha/` | Zerodha (Kite) concrete implementation |
| `tick_ingest/` | Zerodha WebSocket → validated `TickEvent` → Redis pub/sub |
| `candles/` | `TickEvent` → OHLCV `CandleEvent` (bar accumulator + warmup) |
| `strategy/` | Pure signal generators: candle in, `Signal` or `None` out |
| `risk/` | Pre-trade validation, position sizing, circuit-breaker check |
| `execution/` | Order placement, idempotency, fill tracking |
| `worker/` | Redis subscriber + circuit-breaker sync for worker processes |
| `storage/` | All Postgres + Redis persistence (domain stores) |
| `monitoring/` | APScheduler market-hours cron + liveness heartbeats + Telegram alerts |
| `api/` | FastAPI read-only dashboard + Telegram alerter |
| `reports/` | EOD P&L report generation |
| `di/` | Dishka dependency-injection wiring — the composition root |
| `scripts/` | One-off CLI utilities (login, fetch data, import candles, report) |

---

## Two process types

The platform runs as two distinct process types that communicate over Redis:

```
┌─────────────────────────────────────────────────────────┐
│  Ingestor process  (python main.py)                     │
│                                                         │
│  KiteIngestor ──► TickIngestor ──► TickPublisher        │
│       │                                  │              │
│       └──► CandleAggregator         Redis pub/sub       │
│                   │                      │              │
│           (local algo callbacks)         │              │
│           SignalGenerator                │              │
│           RiskFilter                     │              │
│           OrderExecutor                  │              │
└─────────────────────────────────────────┼───────────────┘
                                          │
                              ticks:<token>  circuit:state
                                          │
┌─────────────────────────────────────────▼───────────────┐
│  Worker process  (python main.py worker --algo NAME)    │
│                                                         │
│  TickSubscriber ──► CandleAggregator                    │
│       │                   │                             │
│  RedisCircuitBreaker   SignalGenerator                  │
│                           │                             │
│                       RiskFilter                        │
│                           │                             │
│                       OrderExecutor                     │
└─────────────────────────────────────────────────────────┘
```

**Ingestor** owns the Zerodha WebSocket, validates every tick, writes to Postgres, and publishes to Redis. It can also run algos locally (single-process mode).

**Worker** subscribes to Redis tick channels and runs exactly one named algo. Workers are stateless — they can be restarted without data loss because Postgres holds all state.

---

## Full data flow (tick to order)

```
1. Zerodha WebSocket tick (raw dict, background thread)
        │
        ▼ call_soon_threadsafe → asyncio loop
2. TickIngestor.handle(raw_tick)
   ├── validate token, price, timestamp
   ├── INSERT tick_logs → returns tick_log_id
   └── return TickEvent(tick_log_id, symbol, price, …)
        │
        ├─────────────────────────────────────────────────►
        │                                    TickPublisher
        │                                    PUBLISH ticks:<token>
        │                                    SET circuit:state
        ▼
3. CandleAggregator.on_tick(tick_event)
   ├── BarAccumulator: update partial bar
   └── bar closed? → emit CandleEvent
        │
        ▼
4. SignalGenerator.on_candle(candle_event)
   └── strategy.on_candle(symbol, candle) → Signal | None
        │
        ▼ (if Signal returned)
5. RiskFilter.handle(signal)
   ├── circuit breaker open? → DROP
   ├── daily loss limit exceeded? → DROP
   ├── position already open? → DROP
   ├── intraday cutoff passed? → DROP
   └── calculate_quantity(equity, risk_pct, stop_distance)
       → ValidatedOrderEvent
        │
        ▼
6. OrderExecutor.handle(validated_order)
   ├── is_duplicate(signal_id)? → DROP (idempotency)
   ├── INSERT orders (status=PENDING)
   ├── broker.place_order() → kite_order_id
   ├── UPDATE orders (status=PLACED)
   └── paper trading? simulate fill from PriceStore
       live trading? wait for Kite postback webhook
           └── UPDATE orders (status=FILLED)
               UPDATE positions (SELECT … FOR UPDATE)
```

Every event carries `tick_log_id` from step 2 through to step 6. A single `WHERE tick_log_id = X` query reconstructs the full causal chain for any order.

---

## Component lifecycle

Every long-running service is a `Component` (`core/lifecycle/component.py`). The `Runtime` starts them in a fixed order and stops them in reverse:

```
Startup order (each _setup() completes before the next begins):
  1. TickIngestor          — builds token→symbol lookup tables
  2. KiteIngestor          — opens WebSocket, subscribes to tokens
  3. TickPublisher         — ready to publish
  4. CandleAggregator      — warmup (fetches history, replays to indicators)
  5. HeartbeatMonitor      — begins writing DB heartbeats
  6. DashboardServer        — starts uvicorn last (all deps are running)

Shutdown order (reverse):
  6 → 5 → 4 → 3 → 2 → 1
```

The `Scheduler` (`monitoring/scheduler.py`) is not a `Component` — it's started and stopped directly in `main.py`. It fires `runtime.start()` at 09:15 IST and `runtime.stop()` at 15:30 IST every weekday.

---

## Dependency injection

`di/container.py` is the composition root. Nothing outside `di/` chooses concrete types; all other packages depend only on abstract interfaces. Two container factories exist:

- `build_container()` — ingestor process; wires `ComponentProvider` (KiteIngestor-based)
- `build_worker_container(algo_name)` — worker process; wires `WorkerComponentProvider` (TickSubscriber-based)

The switch between live and paper trading is made entirely inside `BrokerProvider` based on `settings.paper_trading` — no other code changes.

---

## State and persistence

All persistent state lives in Postgres. Redis is ephemeral (tick pub/sub + circuit state).

| What | Where | Written by | Read by |
|------|-------|-----------|--------|
| OHLCV bars | `candles` | `CandleAggregator` | indicators, backtest |
| Tick log | `tick_logs` | `TickIngestor` | audit, dashboard |
| Signals | `signals` | `RiskFilter` | dashboard, reports |
| Orders | `orders` | `OrderExecutor` | dashboard, reports |
| Positions | `positions` | `OrderExecutor` | `RiskFilter`, dashboard |
| Decision log | `decision_logs` | all pipeline stages | audit, dashboard |
| Heartbeats | `heartbeats` | `HeartbeatMonitor` | `HeartbeatMonitor` (stale check) |
| Algo config | `algo_configs` | `ConfigStore.seed` (startup) | `di/providers` |
| Algo state | `algo_states` | `ConfigStore.upsert` | recovery on restart |
| Broker token | `broker_tokens` | `scripts/login.py` | `main.py` startup |

---

## Market-hours schedule (IST, weekdays only)

| Time | Event |
|------|-------|
| 09:15 | `Runtime.start()` — WebSocket connects, warmup runs |
| 15:29 | Position reset (optional) |
| 15:30 | `Runtime.stop()` — WebSocket closes, workers stop |
| 15:45 | EOD report generated |
| Sun 10:00 | Instrument master sync (Zerodha → Postgres) |

---

## Adding a new algo

1. Create a strategy in `strategy/my_strategy.py` (implement `Strategy`, set `alias`).
2. Register it in `strategy/factory.py`.
3. Add an entry to `strategy_config.json` referencing the alias.
4. Set the `ALGOS` env var to include the new algo name.
5. Backtest in `tst/integ/strategy/` — the same pipeline runs unchanged.
6. Deploy as a worker: `python main.py worker --algo my_algo_name`.

---

## Adding a new data source

Replace or supplement `tick_ingest/` by implementing `BrokerStream` (`broker/base/broker_stream.py`) and wiring a new ingestor `Component`. Everything downstream (`candles/`, `strategy/`, `risk/`, `execution/`) is source-agnostic — it only sees `TickEvent` objects.
