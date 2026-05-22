# broker/base/

Abstract interfaces for broker implementations. All concrete brokers (`zerodha/`, `paper_broker.py`) implement these.

## Files

| File | Purpose |
|------|---------|
| `broker.py` | `Broker` ABC — market data + order placement |
| `broker_stream.py` | `BrokerStream` ABC — WebSocket tick streaming |

## Broker

`Broker` defines three methods that every broker implementation must provide:

| Method | Returns | Description |
|--------|---------|-------------|
| `get_instruments(exchange)` | `pl.DataFrame` | Instrument master: symbol, token, type |
| `get_ohlc(symbol, interval, from_dt, to_dt)` | `pl.DataFrame` | Historical OHLCV bars |
| `place_order(order)` | `str` | Place an order; returns broker-assigned ID |

## BrokerStream

`BrokerStream` manages a WebSocket-style streaming feed via three lifecycle methods (`connect`, `subscribe`, `close`) and three callback setters:

- `set_on_connect(fn)` — called when the feed connects
- `set_on_ticks(fn)` — called with each batch of raw tick dicts (runs on the broker's background thread)
- `set_on_disconnect(fn)` — called on disconnection

Callers (e.g., `KiteIngestor`) are responsible for bridging the background-thread callbacks to the asyncio event loop using `call_soon_threadsafe`.
