# risk

Signal validation, position sizing, and risk gate enforcement.

## Layout

```
risk/
├── api/
│   ├── __init__.py       Re-exports: ValidatedOrderEvent, RiskFilter, RiskConfig,
│   │                                 RiskGate, RiskSizer, RiskContext, VolatilitySizer
│   ├── interfaces.py     AbstractPositionStore, AbstractTradingStore, AbstractAuditStore,
│   │                     CacherFactory, SignalEvent protocols
│   └── schemas.py        ValidatedOrderEvent (re-export from core.schemas)
├── service/
│   ├── filter.py         RiskFilter — runs gate chain, sizes, emits ValidatedOrderEvent
│   ├── policy.py         RiskContext dataclass, RiskGate ABC, RiskSizer ABC
│   └── sizer.py          VolatilitySizer — ATR-based quantity calculation
├── storage/
│   └── models.py         (reserved for future equity snapshots)
├── di/
│   └── providers.py      RiskProvider
└── gates/
    ├── circuit_breaker.py  CircuitBreakerGate — rejects when CB is open
    ├── daily_loss.py       DailyLossGate — rejects when daily loss limit exceeded
    ├── duplicate_position.py DuplicatePositionGate — rejects if position already open
    └── time_cutoff.py      TimeCutoffGate — rejects after intraday cutoff time
```

## How RiskFilter works

1. Builds a `RiskContext` (equity, today's PnL from cache, current position)
2. Runs each `RiskGate.check(signal, ctx)` in order — first rejection wins
3. Calls `RiskSizer.size(signal, ctx)` to determine quantity
4. If qty > 0: saves the signal to DB, fires a decision log, returns `ValidatedOrderEvent`
5. Otherwise logs the rejection reason and returns `None`

## Imports

```python
from trading.risk.api import RiskFilter, RiskConfig, ValidatedOrderEvent
```

See [gates/README.md](gates/README.md) for the gate implementations.
