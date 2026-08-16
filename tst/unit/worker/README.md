# tst/unit/worker/

Unit tests for `src/trading/worker/`.

## What is tested

- **`TickAgentComponent`** — faust agent consuming the Kafka `ticks` topic, deserializes `TickEvent`, filters to the algo's instruments, drives `TickPipeline.run`
- **`RedisCircuitBreaker`** — polls `circuit:state` from Redis; verifies local cache updates and that workers respect open/closed state without owning it
