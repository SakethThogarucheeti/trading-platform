# tick_ingest/

Zerodha WebSocket → validated `TickEvent` → Redis pub/sub. Add new market-data sources here.

## Files

| File | Purpose |
|------|---------|
| `tick_ingestor.py` | Validates raw ticks; owns the in-process `CircuitBreaker` |
| `kite_ingestor.py` | WebSocket `Component`; bridges Kite callbacks to asyncio |
| `tick_publisher.py` | Publishes `TickEvent` to Redis and circuit state to `circuit:state` |

## Pipeline

```
Zerodha WebSocket
        ↓  raw dict (KiteTicker callback thread)
KiteIngestor  ─── call_soon_threadsafe ──→  asyncio event loop
        ↓
TickIngestor.handle(raw_tick)
    ├── validate token, price, timestamp
    ├── persist TickLog to DB (returns tick_log_id)
    └── return TickEvent
        ↓
TickPublisher.publish(tick_event)
    ├── serialize and PUBLISH to Redis  ticks:<instrument_token>
    └── SET circuit:state  "open" | "closed"
```

## CircuitBreaker

The ingestor owns a `CircuitBreaker` that tracks WebSocket health:

- **Closed** (normal) — ticks are flowing.
- **Open** (fault) — no ticks received for 30 seconds (disconnection timeout). The `RiskFilter` checks circuit state before placing orders; no orders are sent while the circuit is open.
- **Closed again** — on WebSocket reconnect.

Worker processes use `RedisCircuitBreaker` (`worker/circuit_breaker_redis.py`) which polls `circuit:state` from Redis every 2 seconds instead of owning the state directly.

## KiteIngestor

`KiteIngestor` is a `Component` that:
1. Calls `ZerodhaStream.connect()` and subscribes to instrument tokens from config.
2. Receives ticks on the KiteTicker background thread and bridges them to asyncio via `call_soon_threadsafe`.
3. Invokes `TickIngestor.handle()` for validation + persistence, then `TickPublisher.publish()`.
4. Fires `on_tick` callbacks (e.g., `CandleAggregator`) after each validated tick.

## Relationship to other packages

- `broker/zerodha/stream.py` — `ZerodhaStream` (the raw KiteTicker wrapper)
- `worker/tick_subscriber.py` — consumes `ticks:<token>` channels in worker processes
- `candles/candle_aggregator.py` — registered as `on_tick` callback
- `di/providers/components.py` — wires `KiteIngestor` + `TickIngestor` + `TickPublisher` into the main runtime
