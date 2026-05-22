# tst/unit/worker/

Unit tests for `src/trading/worker/`.

## What is tested

- **`TickSubscriber`** — subscribes to Redis channels, deserializes `TickEvent`, calls `on_tick` callbacks; verified with `fakeredis`
- **`RedisCircuitBreaker`** — polls `circuit:state` from Redis; verifies local cache updates and that workers respect open/closed state without owning it
