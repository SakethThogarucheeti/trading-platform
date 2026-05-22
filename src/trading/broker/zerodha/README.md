# broker/zerodha/

Zerodha (Kite) broker integration — market data, order placement, and WebSocket streaming.

## Files

| File | Purpose |
|------|---------|
| `broker.py` | `ZerodhaBroker` — concrete `Broker` for NSE via Zerodha |
| `kite_client.py` | `KiteClient` — typed wrapper around the `kiteconnect` library |
| `stream.py` | `ZerodhaStream` — `BrokerStream` wrapping `KiteTicker` |

## ZerodhaBroker

Implements the `Broker` interface using `KiteClient`. Key behaviors:

- `get_instruments()` — fetches NSE instrument master, caches the result, returns as Polars DataFrame.
- `get_ohlc()` — maps internal interval names (`"5min"`) to Zerodha strings (`"5minute"`), converts datetime to IST, returns sorted OHLCV rows.
- `place_order()` — delegates to `KiteClient.place_order()`.

## KiteClient

Typed wrapper around the untyped `kiteconnect.KiteConnect` library. Provides a clean interface used by `ZerodhaBroker`, `ZerodhaStream`, and `main.py`:

- `login_url()` / `generate_session()` — OAuth flow
- `set_access_token()` — sets the session token after login
- `instruments(exchange)` — raw instrument list
- `historical_data(token, from_dt, to_dt, interval)` — OHLCV history
- `place_order(params)` — order placement

## ZerodhaStream

Wraps `KiteTicker` (Zerodha's WebSocket feed, which runs on a Twisted reactor in a background thread). Key behaviors:

- `connect()` — starts the background thread and opens the WebSocket.
- `subscribe(tokens)` — subscribes to instrument tokens in FULL quote mode.
- `reconnect()` — closes and reopens the stream, useful after token refresh.
- `_parse_tick()` — normalises raw Kite payloads to the internal `Tick` TypedDict.
