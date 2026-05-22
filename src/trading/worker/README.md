# worker/

Redis subscriber and circuit state sync for worker processes. Add new worker-side pipeline stages here.

## Files

| File | Purpose |
|------|---------|
| `tick_subscriber.py` | Redis pub/sub consumer — worker's counterpart to `KiteIngestor` |
| `circuit_breaker_redis.py` | Polls `circuit:state` from Redis; drop-in for in-process `CircuitBreaker` |

## Why a separate package

The main ingestor process owns the Zerodha WebSocket connection and publishes ticks to Redis. Worker processes (one per algo) run in isolated Python processes and consume from Redis instead. This package contains the worker-side of that split:

- `TickSubscriber` subscribes to `ticks:<token>` channels and forwards each deserialized `TickEvent` to registered `on_tick` callbacks — the same interface as `KiteIngestor`.
- `RedisCircuitBreaker` polls `circuit:state` written by `TickPublisher`, so workers respect the ingestor's circuit state without needing a direct connection.

## TickSubscriber

`TickSubscriber` is a `Component` that:
1. Subscribes to a set of `ticks:<instrument_token>` Redis channels.
2. Runs `circuit_breaker.sync_loop()` as a concurrent background task to keep circuit state fresh.
3. Deserializes each Redis message to a `TickEvent` and calls all registered `on_tick` callbacks.

## RedisCircuitBreaker

`RedisCircuitBreaker` is a drop-in replacement for the ingestor's in-process `CircuitBreaker`. It caches the last-seen state locally and refreshes from Redis every 2 seconds. Worker components call `circuit_breaker.is_open` to check whether to suppress orders.

## Relationship to other packages

- `tick_ingest/tick_publisher.py` — writes to `ticks:<token>` and `circuit:state`
- `core/lifecycle/component.py` — `TickSubscriber` extends `Component`
- `candles/candle_aggregator.py` — registered as `on_tick` callback in worker mode
- `di/providers/worker_components.py` — wires `TickSubscriber` + `RedisCircuitBreaker` into the worker runtime
