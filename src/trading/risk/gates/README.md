# risk/gates/

The five individual rejection gates that make up the risk pipeline. Each gate implements a single `async check(event, ctx) -> str | None` method — returning `None` means the signal passes, returning a string is the rejection reason logged to `decision_logs`.

Gates are composed in order by `RiskRegistry`. A signal must pass all gates before reaching the broker.

## Gates

| File | Class | Rejects when |
|------|-------|-------------|
| `time_cutoff.py` | `TimeCutoffGate` | Current time is after the intraday cutoff (default 15:30 IST) |
| `circuit_breaker.py` | `CircuitBreakerGate` | `circuit.is_open()` — WebSocket feed has been silent > 30s |
| `daily_loss.py` | `DailyLossGate` | Today's realized P&L has exceeded `equity × max_daily_loss_pct / 100` |
| `duplicate_position.py` | `DuplicatePositionGate` | An ENTRY signal in the same direction as an existing open position |

The quantity-sizing check (gate 5) lives in `risk/sizer.py` rather than here because it produces an output (the quantity) rather than just a pass/fail decision.

## Gate interface

```python
async def check(self, event: SignalEvent, ctx: RiskContext) -> str | None:
    ...
```

`RiskContext` carries all read-only state a gate might need: `now`, `cutoff`, `equity`, `max_daily_loss_pct`, `realized_pnl`, `position`, `circuit`. No gate issues DB calls — all required data is fetched once by `RiskRegistry` and bundled into the context before the gate chain runs.

## Relationship to other packages

- `risk/` — `RiskRegistry` composes gates in order and calls each `check()`
- `core/messaging.py` — `AbstractCircuitBreaker` interface used by `CircuitBreakerGate`
- `core/schemas.py` — `SignalEvent`, `Side`, `SignalType`
