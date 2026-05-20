# storage

The only layer that touches PostgreSQL. All other packages call the repository interface; none issue SQLAlchemy queries directly.

## Structure

```
storage/
├── base.py        # AbstractRepository — the full method contract
└── repository.py  # Repository — concrete implementation
```

## `AbstractRepository`

Defines the complete DB API consumed by the rest of the system. All methods are async and accept an `AsyncSession` from the caller — the repository is stateless; transaction management is the caller's responsibility.

Key method groups:

| Group | Methods |
|-------|---------|
| Candles | `save_candle`, `get_candles`, `get_candles_since` |
| Ticks | `log_tick` |
| Signals | `save_signal`, `get_signals` |
| Orders | `save_order`, `get_order_by_kite_id`, `update_order_status`, `update_order_fill` |
| Positions | `get_position`, `update_position` (atomic `SELECT … FOR UPDATE`) |
| PnL | `get_daily_realized_pnl` |
| Audit | `log_heartbeat`, `log_decision`, `log_audit_event` |

## `Repository` implementation

- Candle inserts use `ON CONFLICT DO NOTHING` — safe to replay historical feeds without error.
- All monetary values (prices, PnL) are stored as `Numeric` in Postgres and converted to `Decimal` on read to avoid floating-point drift.
- `update_position` uses `SELECT … FOR UPDATE` to prevent concurrent fills from producing an inconsistent net quantity or average price.
- No business logic lives here — arithmetic (position sizing, PnL calculation) is in `risk/sizer.py` and `reports/pnl.py` respectively.

## Session management

Sessions are created by callers (registries, scripts) via `core.database.get_session()`, which is an async context manager:

```python
async with get_session(engine) as session:
    await repo.save_order(session, order)
    # auto-commit on exit, auto-rollback on exception
```

The repository never opens its own sessions or manages transactions. This keeps it composable: multiple repository calls can share one transaction when atomicity is required.
