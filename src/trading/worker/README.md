# worker

Worker-process entry points. Each worker runs in a separate process from the HTTP server (one process per algo) and consumes ticks from Kafka.

## Files

**`tick_agent.py`** — `TickAgentComponent`. Wraps a faust-streaming `App` with one agent consuming the shared Kafka `ticks` topic, deserialises `TickEvent` JSON, filters to this algo's instruments, and calls `TickPipeline.run()` to drive the candle → signal → risk → execution pipeline. Also updates the `PriceStore` so `PaperBroker` has a current price for fill simulation.

**`circuit_breaker_redis.py`** — `RedisCircuitBreaker`. Polls the `circuit:state` Redis key (a small cross-process flag, unrelated to the tick data path). When the ingestor process opens or closes the breaker it writes that key; this subscriber mirrors the state into the worker process's `CircuitBreaker` instance so both processes stay in sync.

## How the two processes divide work

| Process | Entry point | Responsibilities |
|---------|-------------|-----------------|
| Server | `main.py` / `start.py` | HTTP API, KiteIngestor WebSocket, TickPublisher (Kafka producer), HeartbeatMonitor |
| Worker | `worker/` components | TickAgentComponent (faust Kafka consumer), CandleAggregator, SignalGenerator, RiskFilter, OrderExecutor |

The boundary is the Kafka `ticks` topic. The server produces; each algo's worker consumes independently (its own consumer group) and drives the full strategy → execution pipeline for that algo. Circuit-breaker state is a separate, small cross-process flag that still travels over Redis.
