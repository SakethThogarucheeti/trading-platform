# worker

Worker-process entry points. The worker runs in a separate process from the HTTP server and handles the Redis-side of the pipeline.

## Files

**`tick_subscriber.py`** — `TickSubscriber`. Subscribes to the Redis tick channel, deserialises `TickEvent` JSON, and calls `CandleAggregator.handle()` to drive the strategy pipeline. Also updates the `PriceStore` so `PaperBroker` has a current price for fill simulation.

**`circuit_breaker_redis.py`** — `CircuitBreakerRedis`. Subscribes to the Redis circuit-breaker channel. When the ingestor process opens or closes the breaker it publishes an event; this subscriber mirrors the state into the worker process's `CircuitBreaker` instance so both processes stay in sync.

## How the two processes divide work

| Process | Entry point | Responsibilities |
|---------|-------------|-----------------|
| Server | `main.py` / `start.py` | HTTP API, KiteIngestor WebSocket, TickPublisher, HeartbeatMonitor |
| Worker | `worker/` components | TickSubscriber, CandleAggregator, SignalGenerator, RiskFilter, OrderExecutor |

The boundary is Redis pub/sub on the tick channel. The server publishes; the worker consumes and drives the full strategy → execution pipeline.
