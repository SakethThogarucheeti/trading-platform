# tick_ingest

WebSocket tick ingestion — receives raw market ticks from the broker, applies the circuit breaker, writes audit records, and publishes to Redis.

## Layout

```
tick_ingest/
├── api/
│   ├── __init__.py       Re-exports: TickEvent, TickIngestor, KiteIngestor, TickPublisher,
│   │                                 TickConfig, CircuitBreaker, AbstractAuditStore,
│   │                                 AuditStore, AuditContext, BrokerStream, Tick
│   ├── interfaces.py     AbstractAuditStore, BrokerStream, Tick protocols
│   └── schemas.py        TickEvent (re-export from core.schemas)
├── service/
│   ├── ingestor.py       TickIngestor — AbstractRegistry; validates ticks, checks CB,
│   │                                    logs to AuditStore, calls on_tick handlers
│   │                     CircuitBreaker — open/close state machine
│   │                     TickConfig — configuration model
│   ├── kite_ingestor.py  KiteIngestor — Component; bridges ZerodhaStream WebSocket → TickIngestor
│   └── publisher.py      TickPublisher — publishes TickEvent JSON to Redis channel
├── storage/
│   ├── models.py         TickLog ORM model
│   └── store.py          AuditStore — log_tick(), log_decision(), log_audit()
│                         AuditContext — base dataclass for decision context objects
└── di/
    └── providers.py      TickIngestProvider
```

## Key concepts

**`TickIngestor`** is the registry for tick handlers. `KiteIngestor` feeds it ticks from the Zerodha WebSocket. `TickPublisher` is registered as a handler and fans out to Redis pub/sub for the worker process.

**`CircuitBreaker`** — when the broker connection fails repeatedly, the CB opens and `TickIngestor` rejects further ticks. `CircuitBreakerRedis` worker syncs the CB state across processes via Redis.

**`AuditStore`** owns three tables:
- `tick_logs` — one row per raw tick received
- `decision_logs` — one row per pipeline decision (SIGNAL_GENERATED, SIGNAL_ACCEPTED, etc.), linked to a tick_log_id
- `audit_logs` — free-form module-level log messages

## Imports

```python
from trading.tick_ingest.api import KiteIngestor, TickIngestor, AuditStore, CircuitBreaker
```
