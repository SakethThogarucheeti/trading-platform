# tst/unit/worker/

This directory has no test files of its own — it exists only as an import
namespace (`__init__.py`) for `src/trading/worker/`.

## Where the actual coverage lives

Unit tests for `src/trading/worker/` live in
[`tst/unit/tick_ingest/test_tick_pubsub.py`](../tick_ingest/test_tick_pubsub.py),
alongside `TickPublisher`'s tests (a `tick_ingest` module the worker consumes
from):

- **`TickAgentComponent`** (`TestTickAgentComponent`) — faust agent consuming the Kafka `ticks` topic, deserializes `TickEvent`, filters to the algo's instruments, drives `TickPipeline.run`
- **`RedisCircuitBreaker`** (`TestRedisCircuitBreaker`) — polls `circuit:state` from Redis; verifies local cache updates and that workers respect open/closed state without owning it
