# tst/unit/broker/

Unit tests for `src/trading/broker/`.

## Files

| File | What it tests |
|------|--------------|
| `test_broker_base.py` | `Broker` ABC — verifies abstract methods can be called via `super()` and that a concrete subclass satisfies the interface |
| `test_paper_broker.py` | `PriceStore` (in-memory price tracking), `PaperBroker` (fill simulation, slippage calculation, unique `PAPER_{uuid}` order IDs) |

No real Zerodha API calls — `KiteClient` is mocked where needed.
