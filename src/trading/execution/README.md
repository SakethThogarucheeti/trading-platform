# execution

Order placement, fill handling, and position accounting.

## Layout

```
execution/
├── api/
│   ├── __init__.py       Re-exports: FillEvent, ValidatedOrderEvent, OrderExecutor,
│   │                                 FillHandler, PositionAccountant, PositionLedger,
│   │                                 PositionState, ExecConfig, TradingStore, PositionStore,
│   │                                 NotFoundError
│   ├── interfaces.py     Broker, AbstractTradingStore, AbstractPositionStore, CacherFactory
│   └── schemas.py        FillEvent (re-export from core.schemas)
├── service/
│   ├── executor.py       OrderExecutor — places orders via Broker, logs to TradingStore
│   ├── fill_handler.py   FillHandler — marks the order FILLED, applies the fill via PositionAccountant
│   ├── position_accountant.py  PositionAccountant — updates positions on fill
│   ├── ledger.py         PositionLedger — pure position math; PositionState value type
│   ├── order_reconciler.py  OrderReconciler — periodic poll reconciling client_tag-matched
│   │                         orders against Zerodha's own order book (live trading only)
│   └── idempotency.py    Duplicate signal detection (Postgres-backed)
├── storage/
│   ├── models.py         Order, Position ORM models
│   └── store.py          TradingStore (signals + orders + broker tokens), PositionStore
├── di/
│   └── providers.py      ExecutionProvider
└── fill_webhook.py       FastAPI sub-router for Zerodha postback webhook
```

## Key concepts

**`OrderExecutor`** receives a `ValidatedOrderEvent`, checks idempotency, calls `Broker.place_order`, and saves the `Order` row. Fill notifications (from the postback webhook or the paper broker simulator) go through `OrderExecutor.handle_fill()`, which delegates to `FillHandler`.

**`FillHandler`** is the entry point for fill confirmations — from the Zerodha webhook (`fill_webhook.py`) or from the paper broker's synchronous fill simulation. It calls `PositionAccountant.on_fill()`.

**`PositionLedger`** is pure math: given a current `PositionState` and a fill (qty, price, side), returns the new `PositionState`. No IO.

**`TradingStore`** owns the `signals`, `orders`, and `broker_tokens` tables. `PositionStore` owns `positions`.

**`OrderReconciler`** (live trading only, scheduled every `order_reconcile_interval_mins`) closes the gap between an order that timed out or whose process died before `OrderExecutor.handle()` returned, and what Zerodha's order book actually says happened. Every order carries a `client_tag` echoed back by Kite; the reconciler polls `KiteClient.orders()`, matches by tag, corrects `kite_order_id`, and either applies the fill (`OrderExecutor.handle_fill`) or marks the order terminal (REJECTED/CANCELLED) — never both, and a row naturally drops out of the unresolved set once its status moves off PENDING/`FAILED_*`, so repeated polls are idempotent.

## Imports

```python
from trading.execution.api import OrderExecutor, FillHandler, TradingStore, PositionStore
```
