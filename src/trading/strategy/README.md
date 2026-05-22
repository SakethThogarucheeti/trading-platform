# strategy

Signal generators. Each strategy is a pure function: given a completed candle (and access to indicator values), return a `Signal` or `None`. No database writes, no broker calls.

## Structure

```
strategy/
├── base.py                    # Strategy ABC + Signal dataclass
├── factory.py                 # StrategyFactory — lookup and instantiation
├── signal_generator.py        # SignalGenerator — drives on_candle per algo
├── ema_crossover.py           # EMA fast/slow crossover with ATR stop
├── rsi_mean_reversion.py      # RSI oversold/overbought with ATR filter
├── opening_range_breakout.py  # First N-bar high/low breakout
├── vwap_reversion.py          # VWAP ± N×ATR mean reversion
├── dpo_mean_reversion.py      # DPO-based detrended price oscillator reversion
├── linreg_trend.py            # Linear regression channel trend-following
└── squeeze_breakout.py        # Bollinger/Keltner squeeze breakout
```

## `Strategy` ABC

```python
class Strategy(ABC):
    alias: str                    # class-level; used by StrategyFactory

    def set_store(self, store: CandleStore) -> None: ...   # called before first candle
    def get_state(self) -> dict[str, object]: ...          # dashboard snapshot

    @abstractmethod
    async def on_candle(
        self, symbol: str, instrument_type: InstrumentType, candle: CandleEvent
    ) -> Signal | None: ...
```

## `Signal` dataclass

```python
@dataclass
class Signal:
    symbol: str
    instrument_type: InstrumentType
    side: Side                  # BUY | SELL
    strategy_id: str
    signal_type: SignalType     # ENTRY | EXIT
    stop_distance: float        # > 0; used by RiskRegistry for position sizing
    timestamp: datetime         # bar-close time (pass explicitly in backtests)
    signal_id: UUID             # auto-generated; used for idempotency in ExecRegistry
```

`stop_distance` is the ATR-based distance to the stop-loss. `RiskRegistry` uses it to compute `qty = floor((equity × risk_pct) / stop_distance)`.

## Built-in strategies

### `EmaCrossoverStrategy` (`alias = "ema_crossover"`)
BUY when fast EMA crosses above slow EMA; SELL on reverse cross. Stop distance = `atr_multiplier × ATR`.

### `RsiMeanReversionStrategy` (`alias = "rsi_mean_reversion"`)
BUY when RSI crosses up through oversold threshold; SELL when RSI crosses down through overbought threshold. Requires minimum ATR to avoid low-volatility whipsaws.

### `OpeningRangeBreakoutStrategy` (`alias = "opening_range_breakout"`)
Waits for the first N bars to define a range, then generates ENTRY signals on breakouts above the high or below the low. Flat after the intraday cutoff.

### `VwapReversionStrategy` (`alias = "vwap_reversion"`)
Fades moves that exceed VWAP ± `n_atr × ATR`. BUY when price is below the lower band; SELL when above the upper band.

### `DpoMeanReversionStrategy` (`alias = "dpo_mean_reversion"`)
Uses the Detrended Price Oscillator (DPO) to identify price excursions from a displaced moving average. Signals generated when DPO crosses back toward the center from an extreme.

### `LinregTrendStrategy` (`alias = "linreg_trend"`)
Fits a linear regression channel over a lookback window. Enters in the trend direction when price is within the channel and momentum confirms.

### `SqueezeBreakoutStrategy` (`alias = "squeeze_breakout"`)
Detects Bollinger Band / Keltner Channel squeeze (low volatility compression) and enters in the breakout direction when the squeeze releases.

## Adding a new strategy

1. Create `src/trading/strategy/my_strategy.py` with a class that inherits `Strategy` and sets `alias = "my_strategy"`.
2. Add it to `StrategyFactory` in `factory.py`.
3. Reference `"my_strategy"` in the `ALGOS` env var / `strategy_config.json`.
4. Backtest with `tst/integ/strategy/` — the same pipeline and risk code runs unchanged.

## Design rules

- `on_candle()` must be **pure**: no DB writes, no broker calls, no global state mutations.
- Use `set_store()` to construct indicator instances once per symbol; cache them in `self._inds`.
- Return `None` during warmup (when any indicator returns `None`); the registry handles the skip.
- Pass `timestamp=candle.timestamp` when constructing `Signal` so backtest results are reproducible.
