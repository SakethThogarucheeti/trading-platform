# broker

Broker and market-data abstractions. Every interaction with Zerodha — REST or WebSocket — is encapsulated here so the rest of the system never imports `kiteconnect` directly.

## Structure

```
broker/
├── base/
│   ├── broker.py          # Broker ABC (REST: instruments, OHLC, place_order)
│   └── broker_stream.py   # BrokerStream ABC (WebSocket: subscribe, callbacks)
├── zerodha/
│   ├── broker.py          # ZerodhaBroker — wraps KiteConnect REST API
│   ├── stream.py          # ZerodhaStream — wraps KiteConnect WebSocket
│   ├── kite_client.py     # Thin sync/async bridge around kiteconnect.KiteConnect
│   └── models.py          # TypedDicts for raw Kite API response shapes
└── paper_broker.py        # PaperBroker, AbstractPriceStore, PriceStore
```

## Abstractions

### `Broker` (ABC)

```python
async def get_instruments(exchange: str) -> list[Instrument]
async def get_ohlc(symbol, interval, start, end) -> list[Candle]
async def place_order(symbol, side, qty, order_type, limit_price) -> str  # kite_order_id
```

### `BrokerStream` (ABC)

```python
def subscribe(tokens: list[int]) -> None
def set_on_tick(callback: Callable[[list[dict]], None]) -> None
def set_on_connect(callback: Callable[[], None]) -> None
def set_on_disconnect(callback: Callable[[Exception | None], None]) -> None
def start() -> None
def stop() -> None
```

## Implementations

| Mode | Broker | BrokerStream |
|------|--------|--------------|
| Live | `ZerodhaBroker` | `ZerodhaStream` |
| Paper | `PaperBroker` (wraps ZerodhaBroker, fakes `place_order`) | `ZerodhaStream` (real market data) |
| Backtest | `SlippageFillSimulator` (in strategy-testing/) | `CandlePlayer` (in strategy-testing/) |

## Paper trading

`PaperBroker` passes all read operations through to the real broker but short-circuits `place_order`, returning a synthetic order ID and triggering an immediate simulated fill. Fill price comes from `PriceStore`, which is updated by `KiteIngestor` on every incoming tick.

## Key design notes

- `ZerodhaBroker` caches the instruments list in memory (`_instruments`). Instruments change only at market open, so this is safe for the lifetime of a session.
- `kite_client.py` bridges the synchronous `kiteconnect` library to the async event loop using `anyio.to_thread.run_sync`.
- Raw Kite API responses use `TypedDict` (not Pydantic) because they are external API shapes that are not validated before use — the `TickRegistry` handles validation downstream.
