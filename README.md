# trading-platform

Event-driven intraday trading engine for Indian equity markets, built on Zerodha/Kite. Orchestration layer only — pipeline, broker adapters, and risk-filter execution; strategy and risk-filter content live in [trading-strategy-sdk](https://github.com/SakethThogarucheeti/trading-strategy-sdk) and [trading-risk-sdk](https://github.com/SakethThogarucheeti/trading-risk-sdk). Part of the [algo-trader](https://github.com/SakethThogarucheeti/algo-trader) system.

**Architecture:** Direct in-process pipeline · PostgreSQL persistence · APScheduler market-hours automation · dependency-injector DI · async-first (anyio)

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

Each incoming WebSocket tick flows through a fixed chain of module boundaries in a flat,
in-process function call — no message broker, no worker split. `src/trading/app/pipeline.py`
defines the per-algo wiring (`TickPipeline`/`AlgoPipeline`) and can be read as a single document.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            LIVE TRADING                                 │
│                                                                         │
│  Zerodha WebSocket                                                      │
│       │  raw tick                                                       │
│       ▼                                                                 │
│  ┌─────────────────┐                                                   │
│  │  KiteIngestor   │  tick_ingest/service/kite_ingestor.py             │
│  │   (Component)   │  • bridges WS thread → async loop                 │
│  └────────┬────────┘  • dispatches every registered on_tick callback   │
│           │ raw tick    concurrently (one callback per algo)           │
│           ▼            • updates PriceStore in paper trading           │
│  ┌─────────────────┐                                                   │
│  │  TickIngestor   │  tick_ingest/service/ingestor.py                  │
│  │  (AbstractReg.) │  • validates tick, persists tick_log               │
│  └────────┬────────┘  • owns the CircuitBreaker                        │
│           │ TickEvent  (per-algo TickPipeline.run is one on_tick        │
│           ▼            callback registered on KiteIngestor)            │
│  ┌─────────────────┐                                                   │
│  │ CandleAggregator│  candles/service/ — one shared instance,          │
│  │ (AbstractReg.)  │  wrapped by CandleAggregatorComponent for          │
│  └────────┬────────┘  historical warmup + lifecycle                    │
│           │ CandleEvent(s)                                              │
│           ▼                                                             │
│  ┌─────────────────┐                                                   │
│  │ SignalGenerator │  strategy/service/generator.py — one per algo     │
│  │ (AbstractReg.)  │  • runs the algo's Strategy.on_candle()            │
│  └────────┬────────┘  • quantindicators.PolarsStore feeds indicators   │
│           │ list[SignalEvent]                                          │
│           ▼                                                             │
│  ┌─────────────────┐                                                   │
│  │   RiskFilter    │  risk/service/filter.py — one per algo            │
│  │ (AbstractReg.)  │  • gate chain, then VolatilitySizer                │
│  └────────┬────────┘                                                   │
│           │ ValidatedOrderEvent                                         │
│           ▼                                                             │
│  ┌─────────────────┐                                                   │
│  │  OrderExecutor  │  execution/service/executor.py — one per algo     │
│  │ (AbstractReg.)  │  • places order via Broker                         │
│  └────────┬────────┘  • FillHandler updates positions on fill/postback │
│           │                                                             │
│  ┌────────┴────────┐                                                   │
│  │  Zerodha REST   │                                                   │
│  │  (place_order)  │                                                   │
│  └─────────────────┘                                                   │
│                                                                         │
│  Every stage shares one Postgres DB via per-module store classes       │
│  (TradingStore, AuditStore, CandleDataStore, ...) — no shared           │
│  generic Repository.                                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Pipeline wiring

`TickPipeline`/`AlgoPipeline` (`src/trading/app/pipeline.py`) define the wiring, not a project-root
`pipeline.py` — there is one `TickPipeline` per algo, built by `AlgoPipelineFactory.build_and_wire()`
(`src/trading/di/providers/algo_pipeline.py`) and registered as an `on_tick` callback on the single
shared `KiteIngestor`:

```python
class AlgoPipeline:
    async def run(self, signals: list[SignalEvent]) -> None:
        for signal in signals:
            order = await self._risk_filter.handle(signal)
            if order is not None:
                await self._executor.handle(order)


class TickPipeline:
    async def run(self, tick: TickEvent) -> None:
        candles = await self._candle_registry.handle(tick)
        for candle in candles:
            signals = await self._signal_generator.handle(candle)
            await self._algo_pipeline.run(signals)
```

To add/change an algo: edit `ALGOS` in settings (`AlgoSettings.strategy_id`, `.strategy_params`,
`.risk_gates`, `.instruments`) — strategy and risk-gate selection are config-driven via
`trading_strategy_sdk.factory.create_strategy()` / `trading_risk_sdk.registry.create_gate()`, not
hardcoded. To switch paper/live: `Settings.paper_trading`.

### Component Overview

`Component` (`core/lifecycle/component.py`) is the shared lifecycle base — `CREATED → STARTING
(_setup) → RUNNING (_run) → STOPPING → STOPPED`. `_RuntimeAssembler.build_runtime()`
(`di/containers/components.py`) wires the process's components once at startup:

| Component                | File                                          | What it does |
| ------------------------- | ---------------------------------------------- | ------------- |
| `KiteIngestor`            | `tick_ingest/service/kite_ingestor.py`        | Bridges the Zerodha WebSocket to the async event loop; dispatches every registered per-algo `on_tick` callback concurrently. Manages the circuit-breaker timer on disconnect. |
| `CandleAggregatorComponent` | `candles/service/`                          | Lifecycle wrapper: runs historical warmup via `HistoricalDataService` on startup, then sleeps while live ticks flow in through the shared `CandleAggregator`. |
| `Strategy`                | `trading_strategy_sdk` (`base.py`)            | Abstract base (external package). `on_candle(...)` returns a `Signal` or `None`; `SignalGenerator` calls it per algo. |
| `HeartbeatMonitor`        | `monitoring/service/heartbeat.py`             | Writes its own heartbeat to Postgres every N seconds and checks other modules' `heartbeats` rows. Fires a Telegram alert via `TelegramAlerter` on staleness. |
| `Runtime`                 | `core/lifecycle/runtime.py`                   | Supervises `[KiteIngestor, CandleAggregatorComponent, HeartbeatMonitor]` with ordered startup (each `_setup()` completes before the next starts) and reverse-order shutdown. |
| `Scheduler`               | `monitoring/service/scheduler.py`             | APScheduler wrapper. Fires `Runtime.start` at 09:15 IST and `Runtime.stop` at 15:30 IST on weekdays (`main.py` also fires `Runtime.start` immediately if the process starts mid-session). |
| `ApiServer`               | `api/server.py`                               | Wraps the FastAPI dashboard/REST app as a `Component`, started alongside the rest if `Settings.dashboard_enabled`. |

`OrderExecutor` and `RiskFilter` are not top-level `Component`s — they're plain objects held inside
each algo's `TickPipeline`/`AlgoPipeline`, invoked directly by `TickPipeline.run()`, not scheduled by
`Runtime`.

### Module (AbstractRegistry) Overview

The tick → order chain is a sequence of `AbstractRegistry.handle()` implementations — stateful processors called directly in-process, not message-bus listeners:

| Class             | File                                | Input → Output                                |
| ------------------ | ------------------------------------ | ----------------------------------------------- |
| `TickIngestor`     | `tick_ingest/service/ingestor.py`   | raw tick dict → `TickEvent \| None`            |
| `CandleAggregator` | `candles/service/`                  | `TickEvent` → `list[CandleEvent]`              |
| `SignalGenerator`  | `strategy/service/generator.py`     | `CandleEvent` → `list[SignalEvent]`            |
| `RiskFilter`       | `risk/service/filter.py`            | `SignalEvent` → `ValidatedOrderEvent \| None`  |
| `OrderExecutor`    | `execution/service/executor.py`     | `ValidatedOrderEvent` → `None`                 |

`TickIngestor` owns the `CircuitBreaker`; `RiskFilter` receives the same instance — no copies, no flags, no bus channels.

### Risk Pipeline

Every signal passes through the algo's configured chain of gates (`algo.risk_gates`, resolved via `trading_risk_sdk.registry.create_gate()`), then a sizer — both pluggable per algo, not hardcoded:

```
SignalEvent
    │
    ├─ time_cutoff          reject after the configured intraday cutoff (default 15:30 IST)
    ├─ circuit_breaker      reject while the ingestor's CircuitBreaker is open
    ├─ daily_loss           reject if |today's realized PnL| exceeds max_daily_loss_pct × equity
    │                       (disabled by DI when paper_trading=True)
    ├─ duplicate_position   reject an ENTRY if already positioned in the same direction
    │                       (opposite direction = reversal, allowed through)
    └─ VolatilitySizer      reject (ZERO_QUANTITY) if the ATR-based size rounds to 0
             │
             └─► ValidatedOrderEvent  (quantity already determined)
```

`trading_risk_sdk`'s gate registry currently has four gates (`time_cutoff`, `circuit_breaker`, `daily_loss`, `duplicate_position`); which ones run, in what order, and with what params is entirely config-driven per algo (`RiskFilter` is registry-agnostic — it just iterates whatever gate list it's constructed with).

Position sizing formula (`trading_risk_sdk.sizer.calculate_quantity`):

```
qty = floor( (equity × risk_per_trade_pct / 100) / max(stop_distance, min_stop_floor) )
```

capped by a max-notional-per-trade limit and rounded down to `lot_size` when one is configured. `stop_distance` comes from the strategy (typically `atr_multiplier × ATR`).

### Persistence Model

Every event that flows through the pipeline leaves a trace in Postgres:

| Table           | Written by                                          | Purpose                                         |
| ---------------- | ----------------------------------------------------- | -------------------------------------------------- |
| `tick_logs`     | `TickIngestor`                                       | Immutable record of every raw market tick       |
| `decision_logs` | `CandleAggregator`, `SignalGenerator`, `RiskFilter`  | Full audit trail — one row per pipeline step    |
| `signals`       | `RiskFilter`                                         | Accepted signal parameters                      |
| `orders`        | `OrderExecutor`                                      | Order lifecycle (PENDING → PLACED → FILLED)     |
| `positions`     | `OrderExecutor` / `FillHandler`                      | Live net position per (symbol, instrument_type) |
| `heartbeats`    | `HeartbeatMonitor`                                   | Module liveness timestamps                      |
| `audit_logs`    | `RiskFilter`, `OrderExecutor`, others                | Free-form operational events                    |

Every event carries a `tick_log_id` that propagates from the original tick all the way to the fill. A single query on `decision_logs WHERE tick_log_id = X` reconstructs the full causal chain for any trade.

### Broker Abstraction

The `Broker` and `BrokerStream` ABCs (`broker/api`) allow the execution layer to be swapped without touching any strategy or risk code:

| Mode          | Broker                                                 | BrokerStream                       |
| ------------- | -------------------------------------------------------- | ------------------------------------ |
| Live trading  | `ZerodhaBroker` (REST via `KiteClient`)                  | `ZerodhaStream` (WebSocket)        |
| Paper trading | `PaperBroker` (wraps a real `Broker`, fakes `place_order` against `PriceStore`) | `ZerodhaStream` (real market data) |

Backtesting/research (`CandlePlayer`, `SlippageFillSimulator`) lives outside this repo entirely — in `trading-integ-tests` and `trading-research`, each of which imports `trading-platform` (as an editable path dependency and a tagged dependency respectively) and reuses its live `SignalGenerator`/`RiskFilter`/`OrderExecutor` classes against those simulated implementations. See those repos' own docs, not this one, for the backtest data flow.

### Dependency Injection

The system uses [`dependency_injector`](https://github.com/ets-labs/python-dependency-injector) for DI. `AppContainer` (`di/containers/app.py`) composes three declarative containers:

- **`InfrastructureContainer`** — process-lifetime singletons: `Settings`, `AsyncEngine`/session factory, `PriceStore`, `ValueCache`, and the per-domain storage classes (`CandleDataStore`, `InstrumentStore`, `TradingStore`, `PositionStore`, `AuditStore`, `HeartbeatStore`, `ConfigStore`, `ChartStore`) — there's no single generic `Repository`.
- **`BrokerContainer`** — `ZerodhaBroker` (or `PaperBroker`), `ZerodhaStream`, `KiteClient`.
- **`ComponentContainer`** — one `SignalGenerator` + `RiskFilter` + `OrderExecutor` per algo config (built by `AlgoPipelineFactory` in `di/providers/algo_pipeline.py`, wired by `_RuntimeAssembler.build_runtime()`), plus shared `TickIngestor`, `CandleAggregator`, `HeartbeatMonitor`, `Runtime`, `Scheduler`, and (if enabled) the dashboard `ApiServer`.

Every component depends only on abstract interfaces (`AbstractPriceStore`, `AbstractRuntime`, `AbstractRegistry`). The concrete implementations are only named at the composition root inside the containers.

---

## Tick-to-Fill Walkthrough

This traces a single INFY tick from the Zerodha WebSocket all the way to a filled order, showing exactly which code runs at each step.

**Scenario:** INFY is trading at 1,520. A new bar closes at 1,523, and the EMA-9 has just crossed above EMA-21 for the first time.

---

### Step 1 — Tick arrives from Zerodha WebSocket

```
Zerodha WebSocket thread
  └── KiteIngestor._on_ws_ticks(raw_ticks)
        └── loop.call_soon_threadsafe(_schedule_tick, tick)   # per tick
              └── task group runs KiteIngestor._handle_tick(raw)
```

`KiteIngestor._handle_tick()` runs on the async event loop and calls `TickIngestor.handle(raw)`:

```python
# tick_ingest/service/ingestor.py — TickIngestor.handle()
raw_event = TickEvent(
    instrument_token=12345, instrument_type=InstrumentType.EQUITY,
    last_price=1523.0, volume=8400, timestamp=now, tick_log_id=0,
)
tick_log_id = await self._audit.log_tick(raw_event, symbol="INFY")

return raw_event.model_copy(update={"tick_log_id": tick_log_id})  # tick_log_id=42
```

Back in `KiteIngestor._handle_tick()`, the returned tick updates the price store and is then fanned out — concurrently — to every registered `on_tick` callback, one per algo's `TickPipeline`:

```python
# tick_ingest/service/kite_ingestor.py — KiteIngestor._handle_tick()
tick = await self._tick_registry.handle(raw)
if self._price_store is not None:
    symbol = self._tick_registry.get_symbol(tick.instrument_token) or ""
    if symbol:
        self._price_store.update(symbol, tick.last_price)  # feeds paper-trading fills

async with create_task_group() as tg:
    for callback in self._on_tick_callbacks:
        tg.start_soon(self._run_one_callback, callback, tick)
```

**State after step 1:**

- `tick_logs` row id=42
- `PriceStore["INFY"] = 1523.0`
- `TickEvent(tick_log_id=42)` handed to every algo's `TickPipeline` concurrently

---

### Step 2 — Candle bar closes

Each algo's `TickPipeline` passes the tick to the **same shared** `CandleAggregator.handle(tick)` instance (`candles/service/aggregator.py`) — one `CandleAggregator` serves every algo, so it de-dupes by tick object identity in case more than one pipeline hands it the exact same tick:

```python
# candles/service/aggregator.py — CandleAggregator.handle()
for cached_tick, cached_result in self._recent:      # identity-keyed dedup cache
    if cached_tick is tick:
        return cached_result

sc = self._token_sc.get(tick.instrument_token)        # SymbolConfig for INFY
closed = []
for interval in self._config.intervals:                # e.g. ["1minute"]
    candle = self._accumulator.process(sc, interval, tick)
    if candle is not None:                              # None while the bar is still open
        fire(self._candle_logger.log(candle))           # fire-and-forget persist
        closed.append(candle)

self._recent.append((tick, closed))
return closed                                           # [] most ticks, [CandleEvent] on bar close
```

`BarAccumulator.process()` (`candles/service/bar_accumulator.py`) is what actually tracks the open/high/low/close/volume of the in-progress bar and returns a closed `CandleEvent` only on the tick that rolls into a new bar:

```python
candle = CandleEvent(
    symbol="INFY", instrument_type=InstrumentType.EQUITY, interval="1minute",
    open=1498.0, high=1525.0, low=1495.0, close=1523.0, volume=142000,
    timestamp=bar_close_time,
    tick_log_id=42,      # ← the tick that closed the bar
)
```

The injected `AbstractCandleLogger` (`CandlePersister` in production) saves the candle row and, when `tick_log_id > 0`, also writes a `CANDLE_EMITTED` decision log:

```python
# candles/service/persister.py — CandlePersister.log()
await self._candle.save_candles([{...}])
if event.tick_log_id > 0:
    await self._audit.log_decision(step="CANDLE_EMITTED", tick_log_id=event.tick_log_id, ...)
```

**State after step 2:**

- `candles` row: symbol=INFY, interval=1minute, close=1523.0
- `decision_logs` row: `step=CANDLE_EMITTED, tick_log_id=42`
- `CandleAggregator.handle()` returned `[CandleEvent(tick_log_id=42)]` to this algo's `TickPipeline` (and independently to every other algo's `TickPipeline` sharing the same `CandleAggregator`)

---

### Step 3 — Signal generator updates indicators, strategy fires

The candle is passed to `SignalGenerator.handle(candle)` (`strategy/service/generator.py`), which pushes the bar into a shared `PolarsStore` before invoking the strategy:

```python
# strategy/service/generator.py — SignalGenerator.handle()
instance = self._tick_bar_and_check_ready(candle)   # pushes candle into PolarsStore,
if instance is None:                                #   gates on instance.is_ready(),
    return []                                        #   advances instance.tick_bar()

signal = await instance.strategy.on_candle(candle.symbol, instance.instrument_type, candle)
```

`instance.strategy` is an `EmaCrossoverStrategy` (`trading_strategy_sdk/ema_crossover.py`) — indicators fetch their own history from the store rather than the generator building a feature DataFrame:

```python
# trading_strategy_sdk/ema_crossover.py — EmaCrossoverStrategy.on_candle()
fast = await fast_ind.compute(EMA.Parameters(period=9))    # 1502.1
slow = await slow_ind.compute(EMA.Parameters(period=21))   # 1501.9
atr = await atr_ind.compute(ATR.Parameters(period=14))     # 8.6

prev_fast, prev_slow = self._prev_fast[symbol], self._prev_slow[symbol]  # 1495.2, 1501.4
stop_distance = self._atr_multiplier * atr   # 1.5 × 8.6 = 12.9

if prev_fast < prev_slow and fast > slow:    # crossed from below to above → BUY
    return self._entry_signal(symbol, instrument_type, Side.BUY, stop_distance, candle)
```

Back in `SignalGenerator.handle()`, a non-`None` signal is wrapped in a `SignalEvent` and a `SIGNAL_GENERATED` decision log is fired — this runs whether or not a signal was produced, since rolling indicator state is persisted every bar regardless:

```python
if signal is not None:
    instance.record_signal(self._clock.now())
self._persist_signal_side_effects(instance, candle)   # fire-and-forget rolling-state + algo-state save

signal_event = SignalEvent.from_signal(signal, candle.tick_log_id, algo_name=self._config.algo_name)
fire(self._log_signal(signal_event, self._config.algo_name))   # decision_logs: SIGNAL_GENERATED
return [signal_event]
```

**State after step 3:**

- `decision_logs` row: `step=SIGNAL_GENERATED, tick_log_id=42, signal_id=a1b2...`
- Strategy's rolling indicator state (`prev_fast`/`prev_slow`/`last_atr`/...) persisted for warm restart
- `[SignalEvent(tick_log_id=42)]` returned to this algo's `TickPipeline`

---

### Step 4 — Risk filter validates the signal

The `TickPipeline` passes the `SignalEvent` to `RiskFilter.handle(event)` (`risk/service/filter.py`), which builds a `RiskContext` (today's realized PnL, current position, equity, circuit-breaker state) and then runs it through this algo's **config-driven** list of gates — by default `time_cutoff`, `circuit_breaker`, `daily_loss`, `duplicate_position` (`trading_risk_sdk`'s gate registry):

```python
# risk/service/filter.py — RiskFilter.handle()
ctx = await self._build_context(event)

for gate in self._gates:                       # e.g. TimeCutoffGate, CircuitBreakerGate,
    rejection = await gate.check(event, ctx)    #      DailyLossGate, DuplicatePositionGate
    if rejection is not None:
        await self._reject(event, rejection)
        return None
```

All four gates pass for this signal (well before the 15:30 cutoff, circuit closed, today's realized PnL under the daily-loss limit, no existing INFY position), so sizing runs next — `VolatilitySizer.size()` delegates to `calculate_quantity()` (`trading_risk_sdk/sizer.py`):

```python
# trading_risk_sdk/sizer.py — calculate_quantity()
effective_stop = max(stop_distance, min_stop_floor)               # max(12.9, 0.01) = 12.9
qty = floor((equity * risk_pct / 100.0) / effective_stop)         # floor((100_000×1.0/100)/12.9) = 77
qty = min(qty, floor((equity * max_notional_pct / 100.0) / entry_price))  # floor(20_000/1523) = 13 — binds here
# no lot_size configured for equities → 13
```

The 20%-max-notional cap is what actually binds for this example — a ₹1,523 stock with a comparatively wide 12.9-point stop hits the notional ceiling before the risk-based quantity does. A non-zero quantity means the signal is accepted:

```python
await self._trading.save_signal(event)          # persist signals row
fire(self._log_decision("SIGNAL_ACCEPTED", event, SignalAcceptedContext(qty=13, order_type="MARKET")))
return ValidatedOrderEvent.from_signal_event(event, qty=13)
```

**State after step 4:**

- `signals` row: `id=a1b2..., symbol=INFY, side=BUY, stop_distance=12.9`
- `decision_logs` row: `step=SIGNAL_ACCEPTED, tick_log_id=42`
- `ValidatedOrderEvent` returned to this algo's `TickPipeline`

---

### Step 5 — Order executor places the order

The `TickPipeline` passes the `ValidatedOrderEvent` to `OrderExecutor.handle(event)` (`execution/service/executor.py`):

```python
# execution/service/executor.py — OrderExecutor.handle()
order = Order(
    id=order_id, signal_id=event.signal_id, status=PENDING, qty=13,
    avg_price=Decimal("0"), created_at=now,
    kite_order_id=f"PENDING_{order_id}",   # unique placeholder — never a shared ""
)

if not await self._insert_pending_order(order, event.signal_id):  # idempotency check
    return                                                        # duplicate signal_id → drop

kite_order_id, final_status = await self._place_with_broker(event, order_id)
await self._persist_order_status(order_id, kite_order_id, final_status)
```

`_place_with_broker()` calls `broker.place_order()` and translates any failure to `REJECTED` — a "timed out" error is logged `CRITICAL` and handled distinctly, since the order may actually have reached the broker even though the timeout fired:

```python
kite_order_id = await self._broker.place_order(
    symbol="INFY", side=BUY, qty=13, order_type=MARKET,
    instrument_type="EQUITY", tick_log_id=42,
)
# kite_order_id = "KITE_ORDER_789", status = PLACED
```

`_persist_order_status()` then updates the `orders` row, retrying up to 3 times (`tenacity`) — an `UNRECOVERABLE` critical log fires only if every retry fails, since the order is already live at the broker by this point regardless of whether the DB write succeeds.

For paper trading, `PaperBroker.place_order()` fills immediately at the last price recorded in `PriceStore` and drives the same fill path a live broker webhook would — `OrderExecutor.handle_fill()` delegates straight to `FillHandler.handle()` (`execution/service/fill_handler.py`):

```python
# execution/service/fill_handler.py — FillHandler.handle()
await self._trading.update_order_status(kite_order_id, OrderStatus.FILLED, avg_price)
await self._accountant.apply_fill(fill, Side.BUY, "INFY", "EQUITY")   # PositionAccountant
```

**Final state in Postgres:**

| Table           | Row                                                                    |
| --------------- | ------------------------------------------------------------------------ |
| `tick_logs`     | id=42, symbol=INFY, last_price=1523.0                                  |
| `candles`       | symbol=INFY, interval=1minute, close=1523.0                            |
| `decision_logs` | CANDLE_EMITTED, SIGNAL_GENERATED, SIGNAL_ACCEPTED — all tick_log_id=42 |
| `signals`       | id=a1b2..., side=BUY, stop_distance=12.9                               |
| `orders`        | id=c3d4..., status=FILLED, avg_price=1523.0, qty=13                    |
| `positions`     | symbol=INFY, net_qty=13, avg_price=1523.0                              |

To reconstruct the full decision chain for this trade:

```sql
SELECT step, algo_name, context, created_at
FROM decision_logs
WHERE tick_log_id = 42
ORDER BY created_at;
```

---

## Project Layout

Every domain module under `src/trading/` follows the same `api/` (public contract) + `service/`
(business logic) + `storage/` (ORM models, concrete stores) shape — see
[`src/trading/README.md`](src/trading/README.md) for the full package map and module-SDK
convention. This tree shows the pipeline-relevant files only; each module's own `README.md`
has the complete listing.

```
trading-platform/
├── main.py                              # entry point — DI container, Alembic migrations, APScheduler
├── src/trading/
│   ├── build.py                         # lint (ruff) + full unit test suite runner
│   ├── start.py                         # one-command local dev: docker compose up (Postgres) + launch
│   ├── app/                             # composition root
│   │   ├── pipeline.py                  # TickPipeline / AlgoPipeline — per-algo wiring
│   │   ├── database.py                  # engine factory, session helpers
│   │   └── tasks.py                     # fire() — fire-and-forget task helper
│   ├── config/                          # pydantic-settings (.env) + strategy_config.json loader
│   ├── core/                            # shared primitives, no I/O
│   │   ├── clock.py                     # Clock ABC, SystemClock, SimulatedClock
│   │   ├── messaging.py                 # AbstractRegistry ABC
│   │   ├── models.py                    # cross-cutting SQLAlchemy ORM models (DecisionLog, AuditLog, Heartbeat, ...)
│   │   ├── schemas.py                   # shared Pydantic event models
│   │   └── lifecycle/
│   │       ├── component.py             # Component ABC (CREATED→RUNNING→STOPPED)
│   │       └── runtime.py               # Runtime — ordered anyio TaskGroup startup
│   ├── di/                              # the ONE global DI layer (dependency_injector) — see Dependency Injection above
│   │   ├── containers/                  # app.py, infra.py, broker.py, components.py
│   │   └── providers/                   # algo_pipeline.py (AlgoPipelineFactory), indicators.py
│   ├── tick_ingest/                     # WebSocket tick → validated TickEvent
│   │   └── service/
│   │       ├── ingestor.py              # TickIngestor + CircuitBreaker
│   │       └── kite_ingestor.py         # KiteIngestor — WS lifecycle Component
│   ├── candles/                         # TickEvent → OHLCV CandleEvent
│   │   └── service/
│   │       ├── aggregator.py            # CandleAggregator + CandleAggregatorComponent
│   │       ├── bar_accumulator.py       # BarAccumulator — OHLCV bar math
│   │       ├── persister.py             # CandlePersister — saves candle + CANDLE_EMITTED log
│   │       └── historical.py            # HistoricalDataService — warm-up fetch on startup
│   ├── strategy/                        # CandleEvent → SignalEvent
│   │   └── service/generator.py         # SignalGenerator — runs one Strategy instance per instrument
│   ├── risk/                            # SignalEvent → ValidatedOrderEvent
│   │   └── service/filter.py            # RiskFilter — config-driven gate chain + VolatilitySizer
│   ├── execution/                       # ValidatedOrderEvent → filled Order
│   │   └── service/
│   │       ├── executor.py              # OrderExecutor — place, idempotency, retry
│   │       ├── fill_handler.py          # FillHandler — mark FILLED, apply to position
│   │       ├── position_accountant.py   # PositionAccountant — position/PnL bookkeeping
│   │       ├── eod_square_off.py        # end-of-day open-position close-out
│   │       └── idempotency.py           # signal_id duplicate detection
│   ├── broker/                          # Broker abstraction
│   │   └── service/
│   │       ├── broker.py                # Broker ABC
│   │       ├── paper_broker.py          # PaperBroker — fakes fills against PriceStore
│   │       └── zerodha/                 # ZerodhaBroker (REST) + ZerodhaStream (WebSocket)
│   ├── monitoring/
│   │   └── service/
│   │       ├── heartbeat.py             # HeartbeatMonitor — module liveness + Telegram alerts
│   │       └── scheduler.py             # APScheduler market-hours integration
│   ├── storage/                         # shared indicator/state infrastructure (not a per-module store)
│   │   ├── cache/                       # rolling-state cacher — algo warm restart across process bounces
│   │   └── stores/candle_store.py       # CandleStore — currently unused in production wiring, see #66
│   ├── reports/                         # PnL and trade report generation
│   ├── api/                             # FastAPI HTTP layer
│   │   ├── app.py                       # build_app() — assembles routers into a FastAPI app
│   │   ├── server.py                    # ApiServer — Component wrapper (starts uvicorn)
│   │   ├── telegram.py                  # TelegramAlerter — Telegram Bot API client
│   │   └── routers/                     # one module per domain: auth, market, algos, pnl, reports, charts, stream, broker, data
│   └── scripts/
│       ├── login.py                     # daily Zerodha token refresh
│       ├── fetch_data.py                # download historical OHLCV to Parquet
│       └── import_candles.py            # bulk-load Parquet candles into Postgres
├── alembic/                             # DB migrations
├── tst/unit/                            # unit tests (aiosqlite, no external services) — one dir per src/trading/<module>
├── strategy_config.json                 # per-algo instrument/strategy maps + hyperparameters
└── docker-compose.yml                   # postgres + platform + dashboard
```

Backtesting/Monte Carlo/walk-forward simulation no longer lives in this repo — see the
[Broker Abstraction](#broker-abstraction) subsection above for where `trading-integ-tests` and
`trading-research` picked it up.

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
