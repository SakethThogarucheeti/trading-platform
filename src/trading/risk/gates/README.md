# risk/gates

Pluggable risk gate implementations. Each gate implements `RiskGate.check(signal, ctx) -> str | None` — returning a rejection reason string or `None` to pass.

| File | Gate | Rejects when |
|------|------|-------------|
| `circuit_breaker.py` | `CircuitBreakerGate` | The shared `CircuitBreaker` is open (broker errors tripped it) |
| `daily_loss.py` | `DailyLossGate` | Realized PnL for today is below the configured `max_daily_loss_pct` threshold |
| `duplicate_position.py` | `DuplicatePositionGate` | An open position already exists for the same symbol + instrument type (ENTRY signals only) |
| `time_cutoff.py` | `TimeCutoffGate` | Current time is at or past `intraday_cutoff_hour:intraday_cutoff_minute` |

Gates are constructed and ordered in `di/providers/algo_pipeline.py`. The default order is: `TimeCutoffGate → CircuitBreakerGate → DailyLossGate → DuplicatePositionGate`.
