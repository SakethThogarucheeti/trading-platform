# algo-trader

An event-driven intraday trading platform for Indian equity markets, built on Zerodha/Kite.

**Architecture:** Direct in-process pipeline · PostgreSQL persistence · APScheduler market-hours automation · Dishka DI · async-first (anyio)

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Starting the Bot](#starting-the-bot)
- [Monitoring Dashboard](#monitoring-dashboard)
- [Testing](#testing)
- [System Architecture](#system-architecture)
- [Tick-to-Fill Walkthrough](#tick-to-fill-walkthrough)
- [Project Layout](#project-layout)
- [Adding a New Strategy](#adding-a-new-strategy)
- [Key Design Decisions](#key-design-decisions)

---

## Prerequisites

| Tool                             | Version | Notes                               |
| -------------------------------- | ------- | ----------------------------------- |
| Python                           | 3.13+   | managed by uv via `.python-version` |
| [uv](https://docs.astral.sh/uv/) | latest  | dependency manager and runner       |
| Docker + Docker Compose          | v2+     | for Postgres                        |

---

## Setup

### 1. Clone and install dependencies

```bash
cd trading-platform
uv sync
```

### 2. Configure environment

Copy the example below into `trading-platform/.env` and fill in your values:

```dotenv
# Zerodha credentials — from https://developers.kite.trade/apps
ZERODHA_API_KEY=your_api_key
ZERODHA_API_SECRET=your_api_secret
ZERODHA_ACCESS_TOKEN=          # leave empty; populated by the login script each day

# Infrastructure (match docker-compose defaults)
POSTGRES_URL=postgresql+asyncpg://trading:trading@localhost/trading

# Risk controls (optional — safe defaults shown)
MAX_DAILY_LOSS_PCT=2.0         # halt trading if daily PnL drops this % of equity
RISK_PER_TRADE_PCT=1.0         # risk at most this % of equity per trade

# Paper trading — set to true to simulate orders without hitting Zerodha
PAPER_TRADING=false

# Monitoring — optional Telegram alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Dashboard (optional)
DASHBOARD_ENABLED=true
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8081

# Capital allocated to the default algo (used when ALGOS is not set)
DEFAULT_EQUITY=10000

# Algo configuration — JSON list; omit to use all instruments in the DB with DEFAULT_EQUITY
# ALGOS='[{"name":"momentum","instruments":["INFY","TCS"],"equity":10000}]'
```

> **Zerodha Redirect URL:** In your Kite developer app settings, set the redirect URL to `http://127.0.0.1:8080/` so the login script can capture the request token automatically.

### 3. Daily login (access token refresh)

Zerodha access tokens expire daily. Run this each morning before market open:

```bash
uv run python -m trading.scripts.login
```

This opens a browser to the Kite login page, captures the redirect, and writes `ZERODHA_ACCESS_TOKEN` to `.env` automatically.

---

## Starting the Bot

### One command (recommended)

```bash
uv run start
```

This single command:

1. Starts Postgres via Docker Compose
2. Waits until healthy
3. Launches the trading bot

### Manual steps (if you prefer)

```bash
# 1. Start infrastructure
docker compose up postgres -d

# 2. Wait until healthy, then start the bot
uv run python main.py
```

The bot will:

1. Apply any pending DB migrations automatically
2. Start the APScheduler
3. Fire `Runtime.start` at **09:15 IST** each weekday
4. Fire `Runtime.stop` at **15:30 IST** each weekday
5. If started during market hours, begin trading immediately

Stop with `Ctrl+C` — shuts down cleanly (scheduler stopped, DB connections closed).

### Running everything in Docker (bot + infra)

```bash
docker compose up --build
```

---

## Monitoring Dashboard

When `DASHBOARD_ENABLED=true`, a live portfolio dashboard is available.

| How you're running               | URL                     |
| -------------------------------- | ----------------------- |
| `uv run python main.py` directly | `http://127.0.0.1:8081` |
| `docker compose up`              | `http://localhost:8081` |

---

## Testing

All three test suites use `pytest` via `uv run`. Run them from inside their respective directories.

### Unit tests

Fast, no external services needed (uses `aiosqlite`).

```bash
cd trading-platform
uv run pytest tst/
```

### Strategy tests (backtesting, Monte Carlo, walk-forward)

Requires Docker (uses `testcontainers` to spin up Postgres).

```bash
cd trading-platform/strategy-testing
uv sync
uv run pytest strategy-testing/
```

Individual suites:

```bash
uv run pytest strategy-testing/test_backtest.py            # backtesting
uv run pytest strategy-testing/test_walk_forward.py        # walk-forward analysis
uv run pytest strategy-testing/test_monte_carlo.py         # Monte Carlo simulation
uv run pytest strategy-testing/test_hyperparam_search.py   # EMA crossover grid search
uv run pytest strategy-testing/test_vwap_search.py         # VWAP reversion grid search
uv run pytest strategy-testing/test_rsi_search.py          # RSI mean-reversion grid search
uv run pytest strategy-testing/test_orb_search.py          # Opening range breakout grid search
```

### System / integration tests

Requires Docker. Spins up full infrastructure and tests broker failure, order lifecycle, risk guardrails, and state recovery.

```bash
cd trading-platform/system-testing
uv sync
uv run pytest system-testing/
```

---

## System Architecture

Each incoming WebSocket tick flows through five registry stages in a flat, direct function call — no message broker, no indirection. `pipeline.py` defines the entire flow top-to-bottom and can be read as a single document.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            LIVE TRADING                                 │
│                                                                         │
│  Zerodha WebSocket                                                      │
│       │  raw tick dict                                                  │
│       ▼                                                                 │
│  ┌─────────────────┐                                                   │
│  │  KiteIngestor   │  engine/kite_ingestor.py                          │
│  │   (Component)   │  • bridges WS thread → async loop                 │
│  └────────┬────────┘  • calls TickRegistry.handle(raw)                 │
│           │ TickEvent  • updates PriceStore (paper trading)             │
│           ▼                                                             │
│  ┌─────────────────┐                                                   │
│  │  TickRegistry   │  registry/tick.py                                 │
│  │                 │  • validates tick, persists tick_log               │
│  └────────┬────────┘  • owns CircuitBreaker                            │
│           │ TickEvent                                                   │
│           ▼                                                             │
│  ┌─────────────────┐                                                   │
│  │ CandleRegistry  │  registry/candle.py                               │
│  │                 │  • aggregates ticks into OHLCV bars               │
│  └────────┬────────┘  • returns CandleEvent when bar closes            │
│           │ CandleEvent                                                 │
│           ▼                                                             │
│  ┌─────────────────┐                                                   │
│  │  AlgoRegistry   │  registry/algo.py                                 │
│  │                 │  • feeds candle into FeatureEngine                 │
│  └────────┬────────┘  • calls strategy.on_candle() → Signal            │
│           │ SignalEvent                                                 │
│           ▼                                                             │
│  ┌─────────────────┐                                                   │
│  │  RiskRegistry   │  registry/risk.py                                 │
│  │                 │  • 5-step rejection pipeline                       │
│  └────────┬────────┘  • sizes position, checks circuit breaker         │
│           │ ValidatedOrderEvent                                         │
│           ▼                                                             │
│  ┌─────────────────┐                                                   │
│  │  ExecRegistry   │  registry/exec.py                                 │
│  │                 │  • places order via broker                         │
│  └────────┬────────┘  • simulates fill (paper) or awaits postback      │
│           │                                                             │
│  ┌────────┴────────┐                                                   │
│  │  Zerodha REST   │                                                   │
│  │  (place_order)  │                                                   │
│  └─────────────────┘                                                   │
│                                                                         │
│  All registries share:  Repository (Postgres)                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Pipeline wiring (`pipeline.py`)

`pipeline.py` at the project root is the readable wiring document. It constructs all five registries top-to-bottom and defines `on_tick()` — the single async function called once per incoming WebSocket tick:

```python
async def on_tick(raw: dict) -> None:
    tick = await tick_reg.handle(raw)
    if tick is None:
        return

    candle = await candle_reg.handle(tick)
    if candle is None:
        return

    signals = await algo_reg.handle(candle)
    for signal in signals:
        order = await risk_reg.handle(signal)
        if order is None:
            continue
        await exec_reg.handle(order)
```

To change strategy: edit `instrument_strategy_map` in `pipeline.py`.
To change risk limits: edit `RiskConfig` in `pipeline.py`.
To switch paper/live: change `exec_id` in `TickConfig`.

### Component Overview

| Component              | File                            | What it does                                                                                                     |
| ---------------------- | ------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `KiteIngestor`         | `engine/kite_ingestor.py`       | Bridges the Zerodha WebSocket to the async event loop. Calls `TickRegistry.handle()` for each tick. Manages the circuit-breaker timer on disconnect. |
| `CandleAggregator`     | `engine/candle_aggregator.py`   | Lifecycle wrapper: runs `CandleRegistry.warmup()` on startup (fetches historical candles), then sleeps forever while live ticks flow in. |
| `AlgoRunner`           | `engine/algo_runner.py`         | Lifecycle wrapper around `AlgoRegistry`. Ready at construction time; sleeps forever while candles are fed via `on_candle()`. |
| `TechnicalFeatureEngine` | `features/technical.py`       | Maintains a rolling Polars DataFrame per (symbol, interval). Computes EMA, RSI, ATR, and session VWAP on each `update()` call. |
| `Strategy`             | `strategy/base.py`              | Abstract base. `on_candle(symbol, instrument_type, df)` receives the indicator-enriched DataFrame and returns a `Signal` or `None`. |
| `RiskController`       | `risk/base.py`                  | Lifecycle wrapper around `RiskRegistry`. |
| `OrderExecutor`        | `execution/executor.py`         | Lifecycle wrapper around `ExecRegistry`. |
| `HeartbeatMonitor`     | `engine/heartbeat.py`           | Writes its own heartbeat to Postgres every N seconds and checks all other modules. Fires a Telegram alert when any module goes stale. |
| `Runtime`              | `engine/runtime.py`             | Supervises all components with ordered startup (each component's `_setup()` completes before the next starts) and reverse-order shutdown. |
| `Scheduler`            | `engine/scheduler.py`           | Uses APScheduler to fire `Runtime.start` at 09:15 IST and `Runtime.stop` at 15:30 IST on weekdays. |

### Registry Overview

Each registry stage owns its config (a `@dataclass`) and its `handle()` method. They are stateful processors, not message-bus listeners.

| Registry         | File                  | Input → Output                                 |
| ---------------- | --------------------- | ---------------------------------------------- |
| `TickRegistry`   | `registry/tick.py`    | `dict` → `TickEvent \| None`                   |
| `CandleRegistry` | `registry/candle.py`  | `TickEvent` → `CandleEvent \| None`            |
| `AlgoRegistry`   | `registry/algo.py`    | `CandleEvent` → `list[SignalEvent]`            |
| `RiskRegistry`   | `registry/risk.py`    | `SignalEvent` → `ValidatedOrderEvent \| None`  |
| `ExecRegistry`   | `registry/exec.py`    | `ValidatedOrderEvent` → `None`                 |

`TickRegistry` also owns the `CircuitBreaker`. `RiskRegistry` receives a reference to the same instance — no copies, no flags, no bus channels.

### Risk Pipeline

Every signal passes through five rejection gates in order:

```
SignalEvent
    │
    ├─ 1. Intraday cutoff      reject after 15:30 IST (configurable)
    ├─ 2. Circuit breaker      reject if WebSocket disconnected > 30s
    ├─ 3. Daily loss limit     reject if today's realized PnL ≥ max_daily_loss_pct × equity
    │                          (skipped in paper trading / backtesting)
    ├─ 4. Duplicate position   reject ENTRY if already long/short same direction
    │                          (opposite direction = reversal, allowed through)
    └─ 5. Quantity sizing      reject if ATR-based position size rounds to 0
             │
             └─► ValidatedOrderEvent  (quantity already determined)
```

Position sizing formula:

```
qty = floor( (equity × risk_per_trade_pct / 100) / stop_distance )
```

`stop_distance` comes from the strategy (typically `atr_multiplier × ATR`), so the system automatically risks the same percentage of equity regardless of volatility.

### Persistence Model

Every event that flows through the pipeline leaves a trace in Postgres:

| Table           | Written by                                        | Purpose                                         |
| --------------- | ------------------------------------------------- | ----------------------------------------------- |
| `tick_logs`     | `TickRegistry`                                    | Immutable record of every raw market tick       |
| `decision_logs` | `CandleRegistry`, `AlgoRegistry`, `RiskRegistry`  | Full audit trail — one row per pipeline step    |
| `signals`       | `RiskRegistry`                                    | Accepted signal parameters                      |
| `orders`        | `ExecRegistry`                                    | Order lifecycle (PENDING → PLACED → FILLED)     |
| `positions`     | `ExecRegistry`                                    | Live net position per (symbol, instrument_type) |
| `heartbeats`    | `HeartbeatMonitor`                                | Module liveness timestamps                      |
| `audit_logs`    | `RiskRegistry`, `ExecRegistry`                    | Free-form operational events                    |

Every event carries a `tick_log_id` that propagates from the original tick all the way to the fill. A single query on `decision_logs WHERE tick_log_id = X` reconstructs the full causal chain for any trade.

### Broker Abstraction

The `Broker` and `BrokerStream` ABCs allow the execution layer to be swapped without touching any strategy or risk code:

| Mode          | Broker                                                 | BrokerStream                       |
| ------------- | ------------------------------------------------------ | ---------------------------------- |
| Live trading  | `ZerodhaBroker` (REST via KiteClient)                  | `ZerodhaStream` (WebSocket)        |
| Paper trading | `PaperBroker` (wraps real broker, fakes `place_order`) | `ZerodhaStream` (real market data) |
| Backtesting   | `SlippageFillSimulator`                                | `CandlePlayer` (file replay)       |

### Dependency Injection

The system uses [Dishka](https://github.com/reagento/dishka) for DI. Everything is assembled in three providers:

- **`InfrastructureProvider`** — singletons: Settings, AsyncEngine, `Repository`, `PriceStore`
- **`BrokerProvider`** — `ZerodhaBroker` (or `PaperBroker`), `ZerodhaStream`, `KiteClient`
- **`ComponentProvider`** — one `AlgoRunner` + `RiskController` + `OrderExecutor` per algo config; shared `KiteIngestor`, `CandleAggregator`, `HeartbeatMonitor`, `Runtime`, `Scheduler`

Every component depends only on abstract interfaces (`AbstractRepository`, `AbstractPriceStore`, `AbstractRuntime`). The concrete implementations are only named at the composition root inside the providers.

### Backtesting

The backtest reuses every live registry — `AlgoRegistry`, `RiskRegistry`, `ExecRegistry` — with only the data source and broker swapped:

| Live                                  | Backtest                                      |
| ------------------------------------- | --------------------------------------------- |
| `ZerodhaStream` WebSocket             | `CandlePlayer` replaying Parquet files        |
| `ZerodhaBroker.place_order()`         | `SlippageFillSimulator.place_order()`         |
| `SystemClock` (wall time)             | `SimulatedClock` (bar timestamps)             |
| Real Postgres schema                  | Isolated per-run Postgres schema              |

Because the same registry and strategy code runs in both modes, backtest results directly reflect live behaviour.

---

## Tick-to-Fill Walkthrough

This traces a single INFY tick from the Zerodha WebSocket all the way to a filled order, showing exactly which code runs at each step.

**Scenario:** INFY is trading at 1,520. A new 15-minute bar closes at 1,523, and the EMA-9 has just crossed above EMA-21 for the first time.

---

### Step 1 — Tick arrives from Zerodha WebSocket

```
Zerodha WebSocket thread
  └── ZerodhaStream._on_ticks(raw_ticks)
        └── loop.run_coroutine_threadsafe(_handle_tick, raw)
```

`KiteIngestor._handle_tick()` runs on the async event loop and calls `TickRegistry.handle(raw)`:

```python
# registry/tick.py — TickRegistry.handle()
tick_log_id = await self._repo.log_tick(session, raw_event, symbol)

return TickEvent(
    instrument_token=12345, last_price=1523.0, volume=8400,
    timestamp=now, tick_log_id=42          # ← assigned by DB
)
```

Back in `KiteIngestor`:

```python
# engine/kite_ingestor.py — KiteIngestor._handle_tick()
tick = await self._tick_registry.handle(raw)
if self._price_store is not None:
    self._price_store.update("INFY", tick.last_price)  # paper trading fill simulation
```

**State after step 1:**

- `tick_logs` row id=42
- `PriceStore["INFY"] = 1523.0`
- `TickEvent(tick_log_id=42)` returned to `on_tick()`

---

### Step 2 — Candle bar closes

`on_tick()` passes the `TickEvent` to `CandleRegistry.handle(tick)`:

```python
# registry/candle.py — CandleRegistry.handle()
bar_open = _bar_open_time(tick.timestamp, interval="1min")

# Bar not yet closed — update partial bar in memory
partial.close = 1523.0
partial.high = max(partial.high, 1523.0)
partial.volume += 8400
```

When a new bar opens (different `bar_open`), the previous bar is closed and returned:

```python
candle = CandleEvent(
    symbol="INFY", interval="1min",
    open=1498.0, high=1525.0, low=1495.0, close=1523.0, volume=142000,
    timestamp=bar_close_time,
    tick_log_id=42      # ← the tick that closed the bar
)
# fire-and-forget: log_candle() writes decision_logs row
asyncio.get_running_loop().create_task(self._log_candle(candle))
return candle
```

**State after step 2:**

- `decision_logs` row: `step=CANDLE_EMITTED, tick_log_id=42`
- `CandleEvent(tick_log_id=42)` returned to `on_tick()`

---

### Step 3 — Feature engine updates, strategy fires

`on_tick()` passes the `CandleEvent` to `AlgoRegistry.handle(candle)`:

```python
# registry/algo.py — AlgoRegistry.handle()
df = instance.feature_engine.update(candle)
# df is now a rolling Polars DataFrame with columns:
#   timestamp, open, high, low, close, volume,
#   ema_9, ema_21, rsi_14, atr_14, vwap
```

`TechnicalFeatureEngine.update()` appends the new bar and recomputes all indicators in two Polars passes. The last two rows look like:

```
timestamp   close   ema_9    ema_21   atr_14
09:00       1498    1495.2   1501.4   8.3     ← ema_9 below ema_21
09:15       1523    1502.1   1501.9   8.6     ← ema_9 now above ema_21 ✓
```

The strategy sees the crossover:

```python
# strategy/ema_crossover.py — EmaCrossoverStrategy.on_candle()
prev_fast, cur_fast = 1495.2, 1502.1
prev_slow, cur_slow = 1501.4, 1501.9

# Crossover: was below, now above → BUY signal
if prev_fast < prev_slow and cur_fast > cur_slow:
    return Signal(
        symbol="INFY", side=Side.BUY, strategy_id="ema_crossover",
        signal_type=SignalType.ENTRY,
        stop_distance=1.5 * 8.6   # atr_multiplier × ATR = 12.9
    )
```

`AlgoRegistry` wraps the `Signal` in a `SignalEvent`:

```python
signal_event = SignalEvent(
    symbol="INFY", side=BUY, stop_distance=12.9,
    tick_log_id=42,    # ← still propagating
    signal_id=UUID("a1b2...")
)
asyncio.get_running_loop().create_task(self._log_signal(signal_event, ...))
return [signal_event]
```

**State after step 3:**

- `decision_logs` row: `step=SIGNAL_GENERATED, tick_log_id=42, signal_id=a1b2...`
- `[SignalEvent(tick_log_id=42)]` returned to `on_tick()`

---

### Step 4 — Risk registry validates the signal

`on_tick()` passes each signal to `RiskRegistry.handle(signal)`:

```python
# registry/risk.py — RiskRegistry.handle()

# 1. Time check — 09:15 IST, well before the 15:30 cutoff ✓
# 2. Circuit breaker — tick_reg.circuit.is_open() == False ✓
# 3. Daily loss limit — paper_trading=False, today's PnL = 0, limit = 2,000 ✓
# 4. Position check — no existing INFY position ✓
# 5. Quantity sizing:
qty = floor((100_000 × 1.0 / 100) / 12.9) = floor(775.2) = 775
```

Signal accepted:

```python
await self._repo.save_signal(session, signal_event)   # persist Signal row
return ValidatedOrderEvent(
    signal_id=UUID("a1b2..."), symbol="INFY",
    side=BUY, quantity=775, order_type=MARKET,
    tick_log_id=42
)
```

**State after step 4:**

- `signals` row: `id=a1b2..., symbol=INFY, side=BUY, stop_distance=12.9`
- `decision_logs` row: `step=SIGNAL_ACCEPTED, tick_log_id=42`
- `ValidatedOrderEvent` returned to `on_tick()`

---

### Step 5 — Execution registry places the order

`on_tick()` passes the `ValidatedOrderEvent` to `ExecRegistry.handle(order)`:

```python
# registry/exec.py — ExecRegistry.handle()

# 1. Idempotency: no existing Order for signal_id a1b2... → proceed

# 2. Persist PENDING order (before broker call)
order = Order(id=UUID("c3d4..."), signal_id=UUID("a1b2..."),
              status=PENDING, qty=775)
await self._repo.save_order(session, order)

# 3. Place the order (async REST call to Zerodha)
kite_order_id = await self._broker.place_order(
    symbol="INFY", side=BUY, qty=775, order_type=MARKET
)
# kite_order_id = "KITE_ORDER_789"

# 4. Update order status to PLACED
row.kite_order_id = "KITE_ORDER_789"
row.status = PLACED
```

For paper trading (`exec_id="paper"`), a fill is simulated immediately from `PriceStore`:

```python
# Paper only: simulate fill at last known price
fill_price = self._price_store.get("INFY")   # 1523.0
await self._handle_fill(kite_order_id="KITE_ORDER_789", avg_price=1523.0, ...)
```

**Final state in Postgres:**

| Table           | Row                                                                    |
| --------------- | ---------------------------------------------------------------------- |
| `tick_logs`     | id=42, symbol=INFY, last_price=1523.0                                  |
| `decision_logs` | CANDLE_EMITTED, SIGNAL_GENERATED, SIGNAL_ACCEPTED — all tick_log_id=42 |
| `signals`       | id=a1b2..., side=BUY, stop_distance=12.9                               |
| `orders`        | id=c3d4..., status=FILLED, avg_price=1523.0, qty=775                   |
| `positions`     | symbol=INFY, net_qty=775, avg_price=1523.0                             |

To reconstruct the full decision chain for this trade:

```sql
SELECT step, algo_name, context, created_at
FROM decision_logs
WHERE tick_log_id = 42
ORDER BY created_at;
```

---

## Project Layout

```
trading-platform/
├── main.py                              # process entry point (scheduler, DI, migrations)
├── pipeline.py                          # data flow wiring — read this to understand the system
├── src/trading/
│   ├── config/
│   │   ├── settings.py                  # all config via pydantic-settings + .env
│   │   └── strategy_config.py           # strategy_config.json loader
│   ├── core/
│   │   ├── models.py                    # SQLAlchemy ORM models
│   │   ├── schemas.py                   # Pydantic event models (TickEvent → FillEvent)
│   │   ├── messaging.py                 # AbstractRegistry ABC
│   │   ├── database.py                  # engine factory, session helpers
│   │   └── clock.py                     # Clock ABC, SystemClock, SimulatedClock
│   ├── broker/
│   │   ├── base/broker.py               # Broker ABC
│   │   ├── base/broker_stream.py        # BrokerStream ABC
│   │   ├── zerodha/                     # Zerodha live implementation
│   │   │   ├── broker.py                # ZerodhaBroker (REST)
│   │   │   ├── stream.py                # ZerodhaStream (WebSocket)
│   │   │   ├── kite_client.py           # KiteConnect wrapper
│   │   │   └── models.py                # TypedDicts for Kite API responses
│   │   └── paper_broker.py              # PaperBroker + AbstractPriceStore + PriceStore
│   ├── registry/                        # Pipeline stages — each owns its config + handle()
│   │   ├── tick.py                      # TickConfig + TickRegistry + CircuitBreaker
│   │   ├── candle.py                    # CandleConfig + CandleRegistry
│   │   ├── algo.py                      # AlgoConfig + AlgoRegistry
│   │   ├── risk.py                      # RiskConfig + RiskRegistry
│   │   └── exec.py                      # ExecConfig + ExecRegistry
│   ├── engine/                          # Async runtime + component lifecycle wrappers
│   │   ├── component.py                 # Component ABC (CREATED→RUNNING→STOPPED)
│   │   ├── runtime.py                   # AbstractRuntime + Runtime (ordered lifecycle)
│   │   ├── kite_ingestor.py             # KiteIngestor — WS → TickRegistry
│   │   ├── candle_aggregator.py         # CandleAggregator — warmup + sleep
│   │   ├── algo_runner.py               # AlgoRunner — lifecycle wrapper
│   │   ├── scheduler.py                 # APScheduler market-hours integration
│   │   └── heartbeat.py                 # HeartbeatMonitor + Telegram alerts
│   ├── features/
│   │   ├── base.py                      # FeatureEngine ABC
│   │   └── technical.py                 # TechnicalFeatureEngine (EMA, RSI, ATR, VWAP)
│   ├── strategy/
│   │   ├── base.py                      # Strategy ABC + Signal dataclass
│   │   ├── ema_crossover.py             # EMA crossover strategy
│   │   ├── rsi_mean_reversion.py        # RSI mean-reversion strategy
│   │   ├── vwap_reversion.py            # VWAP reversion strategy
│   │   └── opening_range_breakout.py    # Opening range breakout strategy
│   ├── risk/
│   │   ├── base.py                      # RiskController lifecycle wrapper
│   │   └── sizer.py                     # ATR-based position sizer
│   ├── execution/
│   │   ├── base.py                      # ExecutionEngine ABC
│   │   ├── executor.py                  # OrderExecutor lifecycle wrapper
│   │   └── idempotency.py               # signal_id duplicate detection
│   ├── storage/
│   │   ├── base.py                      # AbstractRepository
│   │   └── repository.py                # Repository (all DB operations)
│   ├── monitoring/
│   │   └── heartbeat.py                 # HeartbeatMonitor — module liveness + Telegram alerts
│   ├── api/
│   │   ├── telegram.py                  # TelegramAlerter — Telegram Bot API client
│   │   ├── routers/                     # FastAPI route modules (one per domain)
│   │   │   ├── auth.py                  # /api/auth/*
│   │   │   ├── market.py                # /api/ping, /api/health, /api/positions, /api/signals, /api/candles, /api/ticks
│   │   │   ├── algos.py                 # /api/algos*
│   │   │   ├── pnl.py                   # /api/pnl, /api/pnl/by-algo
│   │   │   ├── reports.py               # /api/reports/*
│   │   │   ├── charts.py                # /api/charts
│   │   │   ├── stream.py                # /api/decisions/stream (SSE)
│   │   │   ├── broker.py                # /api/postback (Zerodha webhook)
│   │   │   └── data.py                  # /api/sessions, /api/settings, /api/instruments, /api/trades
│   │   └── dashboard/
│   │       ├── app.py                   # build_app() — assembles routers into a FastAPI app
│   │       └── component.py             # DashboardServer — Component wrapper (starts uvicorn)
│   ├── di/
│   │   ├── container.py                 # Dishka container builder
│   │   └── providers/
│   │       ├── infra.py                 # Settings, DB, Repository, PriceStore
│   │       ├── broker.py                # Broker, BrokerStream, KiteClient
│   │       ├── components.py            # Runtime, all components + registries
│   │       ├── features.py              # make_feature_engine() factory
│   │       └── strategy.py              # make_strategy() factory
│   └── scripts/
│       ├── login.py                     # daily Zerodha token refresh
│       └── fetch_data.py                # download historical OHLCV to Parquet
├── alembic/                             # DB migrations
├── tst/unit/                            # unit tests (aiosqlite, no external services)
├── strategy-testing/
│   ├── testing/
│   │   ├── backtesting/engine.py        # BacktestSession
│   │   ├── backtesting/metrics.py       # Sharpe, CAGR, max drawdown, etc.
│   │   ├── monte_carlo/                 # Monte Carlo simulation
│   │   └── simulators/
│   │       ├── candle_player.py         # replays Parquet files as CandleEvents
│   │       └── execution_sim.py         # SlippageFillSimulator
│   └── strategy-testing/               # test files (grid searches, walk-forward)
├── system-testing/                      # Docker-based integration tests
├── strategy_config.json                 # hyperparam search grids + strategy defaults
└── docker-compose.yml
```

---

## Adding a New Strategy

1. **Create the strategy class** in `src/trading/strategy/my_strategy.py`:

```python
from trading.strategy.base import Signal, Strategy
from trading.core.schemas import InstrumentType, Side, SignalType
import polars as pl

class MyStrategy(Strategy):
    @property
    def id(self) -> str:
        return "my_strategy"

    def on_candle(self, symbol, instrument_type, df):
        if df.height < 2:
            return None
        # Your logic here — df has columns: close, ema_9, ema_21, rsi_14, atr_14, vwap
        # Return a Signal or None
        ...
```

2. **Register it** in `src/trading/di/providers/strategy.py`:

```python
case "my_strategy":
    return MyStrategy(**params)
```

3. **Configure it** in `pipeline.py` or via the `ALGOS` env var:

```python
algo_config = AlgoConfig(
    instrument_strategy_map={"INFY": "my_strategy"},
    instrument_feature_map={"INFY": "technical"},
    ...
)
```

4. **Backtest it** — the existing `BacktestSession` will run it automatically with the same risk and execution logic as live trading.

---

## Key Design Decisions

**Direct function calls, not a message bus.** Each tick flows through `on_tick()` as a straight chain of `await registry.handle(event)` calls. There is no Redis pub/sub, no channel names to remember, no subscription management. The entire data flow is visible in 20 lines of `pipeline.py`.

**Registries own their config.** Each pipeline stage is a single file with a `@dataclass` config and a registry class. `TickConfig` + `TickRegistry` live in `registry/tick.py`. Reading one file tells you everything about that stage — what it needs, what it produces, and what it persists.

**CircuitBreaker flows by reference, not by flag.** `TickRegistry` creates the `CircuitBreaker` and exposes it as `tick_reg.circuit`. `RiskRegistry` receives the same object at construction time. When the WebSocket drops, `TickRegistry` starts a 30-second timer; `RiskRegistry` reads `circuit.is_open()` directly. No shared state store, no flag keys to mistype.

**tick_log_id flows through the entire pipeline.** Every event from `TickEvent` to `FillEvent` carries the `tick_log_id` of the originating market tick. The `decision_logs` table uses it as a foreign key, so a single SQL query on `tick_log_id` reconstructs the complete causal chain: which tick triggered which candle, which candle triggered which signal, which signal was accepted or rejected and why, and which order was placed as a result.

**Backtests reuse live code exactly.** `AlgoRegistry`, `RiskRegistry`, and `ExecRegistry` run unchanged in backtests. The only differences are the data source (`CandlePlayer` instead of WebSocket), the broker (`SlippageFillSimulator` instead of Zerodha), and the clock (`SimulatedClock` instead of wall time). If a strategy behaves differently in backtesting than in live trading, it is a data or timing difference, not a code difference.

**Ordered startup prevents race conditions.** `Runtime` starts components sequentially: each component's `_setup()` must complete before the next one begins. `KiteIngestor` is connected and subscribed before `CandleAggregator` runs its warmup, which completes before `AlgoRunner` starts. No component can miss events from its upstream dependency.

**Position updates are atomic.** Order status and position changes happen in a single SQLAlchemy transaction. Concurrent fills for the same symbol cannot race and produce an inconsistent position.
