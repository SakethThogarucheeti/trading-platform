# risk

Pre-order risk controls and position sizing. The five rejection gates in `RiskRegistry` are the system's safety layer — every signal must pass all of them before reaching the broker.

## Structure

```
risk/
├── base.py        # RiskController — Component wrapper around RiskRegistry
├── controller.py  # Risk check registry (composable check chain)
└── sizer.py       # calculate_quantity() — ATR-based position sizer
```

## Risk gates (in order)

### 1. Intraday cutoff
Rejects signals after `intraday_cutoff_hour:minute` (default 15:30 IST). Prevents entering new positions that can't be exited intraday. Configurable per `RiskConfig`.

### 2. Circuit breaker
Rejects signals when `circuit_breaker.is_open()` is `True` — meaning the Zerodha WebSocket has been silent for more than 30 seconds. The circuit breaker instance is the same object held by `TickRegistry`; no flags or shared stores are involved.

### 3. Daily loss limit
Fetches today's realized PnL from the repository and rejects if it has crossed `−(equity × max_daily_loss_pct / 100)`. Skipped in paper trading and backtesting (no reliable intraday PnL available).

### 4. Duplicate position check
Rejects ENTRY signals when the current position is already in the same direction. A SELL signal when long is a reversal and is allowed through. This prevents doubling up on a losing position but permits managed exits.

### 5. Quantity sizing
Computes `qty = floor((equity × risk_per_trade_pct / 100) / stop_distance)`. Rejects if `qty` rounds to zero (signal's stop distance is too wide relative to available capital). For futures/options, rounds down to the nearest lot size multiple.

## `calculate_quantity`

```python
def calculate_quantity(
    stop_distance: float,
    equity: float,
    risk_pct: float,
    lot_size: int = 1,
) -> int
```

Pure function with no DB calls. The formula ensures a fixed percentage of equity is risked regardless of the instrument's volatility, since `stop_distance` is typically `atr_multiplier × ATR`.

## `RiskController`

`Component` wrapper around `RiskRegistry`. Holds no async work of its own (`_run()` sleeps forever). Its `_setup()` reads the current day's PnL and primes the daily loss state so the first signal of the session gets an accurate check.
