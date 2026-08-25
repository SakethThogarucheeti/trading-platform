# risk

Signal validation, position sizing, and risk gate enforcement.

## Layout

```
risk/
├── api/
│   ├── __init__.py       Re-exports: ValidatedOrderEvent, RiskFilter, RiskConfig,
│   │                                 RiskGate, RiskSizer, RiskContext, VolatilitySizer
│   ├── interfaces.py     AbstractPositionStore, AbstractTradingStore, AbstractAuditStore,
│   │                     SignalEvent protocols
│   └── schemas.py        ValidatedOrderEvent (re-export from core.schemas)
├── service/
│   └── filter.py         RiskFilter — runs gate chain, sizes, emits ValidatedOrderEvent
├── storage/
│   └── models.py         (reserved for future equity snapshots)
└── di/
    └── providers.py      RiskProvider
```

`RiskContext`, `RiskGate`, `RiskSizer`, and `VolatilitySizer` now live in `trading_risk_sdk` —
`risk/api/__init__.py` imports them directly, there's no local wrapper. The gate implementations
(`CircuitBreakerGate`, `DailyLossGate`, `DuplicatePositionGate`, `TimeCutoffGate`) also live there
but aren't imported through `risk/api`: they're resolved by config-driven `gate_id` via
`trading_risk_sdk.registry.create_gate()`, called from `di/providers/algo_pipeline.py`.

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
