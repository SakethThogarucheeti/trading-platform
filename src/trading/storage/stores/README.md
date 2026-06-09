# storage/stores

Previously contained all domain store classes. After the SDK migration these have all moved to their owning module's `storage/` layer.

The only file remaining here is `candle_store.py`, which is shared infrastructure (not a domain store):

**`candle_store.py`** — `CandleStore` — implements `quantindicators.AbstractCandleStore` on top of `CandleDataStore` with an optional Redis read-through cache. Used by the indicator library during strategy `on_candle()` execution.

All other stores are now at canonical paths:

```python
from trading.tick_ingest.storage.store import AuditStore
from trading.candles.storage.store import CandleDataStore, InstrumentStore
from trading.execution.storage.store import TradingStore, PositionStore, NotFoundError
from trading.monitoring.storage.store import HeartbeatStore
from trading.strategy.storage.store import ChartStore, ConfigStore
```
