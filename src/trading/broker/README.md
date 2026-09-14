# broker

Broker abstraction layer — defines the `Broker` and `BrokerStream` ABCs and provides two concrete implementations: the live Zerodha adapter and an in-process paper broker for testing and simulation.

## Layout

```
broker/
├── api/
│   ├── __init__.py       Re-exports: Broker, BrokerStream, AbstractPriceStore, PaperBroker, PriceStore, Tick
│   └── schemas.py        Tick (TypedDict — normalised tick from ZerodhaStream)
├── service/
│   ├── broker.py         Broker ABC — get_instruments, get_ohlc, place_order
│   ├── broker_stream.py  BrokerStream ABC — start/stop WebSocket tick feed
│   ├── paper_broker.py   AbstractPriceStore (ABC) + PriceStore + PaperBroker (in-memory fill simulation)
│   └── zerodha/
│       ├── broker.py     ZerodhaBroker (live implementation)
│       ├── kite_client.py KiteConnect HTTP wrapper
│       ├── models.py     KiteOrder, KitePosition typed dicts
│       └── stream.py     ZerodhaStream (KiteTicker WebSocket adapter)
└── storage/
    └── models.py         BrokerToken ORM model (encrypted credential storage)
```

There is no `broker/di/` — live-vs-paper selection is wired in `trading.di.containers.broker.build_broker()` (see `di/README.md`), not a dedicated provider module in this package.

## Key abstractions

**`Broker`** (service/broker.py) — the interface every order-routing component depends on: `get_instruments`, `get_ohlc`, `place_order`. Concrete impls: `ZerodhaBroker` (live) and `PaperBroker` (simulation).

**`BrokerStream`** (service/broker_stream.py) — WebSocket tick feed. `ZerodhaStream` wraps KiteTicker; `PaperBroker` doubles as a synthetic stream for backtests.

**`AbstractPriceStore`** (service/paper_broker.py) — an ABC that gives components read access to the latest tick price. Implemented by `PriceStore` (in-memory dict), updated directly by `KiteIngestor` as validated ticks arrive.

## Credential storage

`BrokerToken` stores the Zerodha access token encrypted with `pgp_sym_encrypt`. The encryption key comes from `TOKEN_SECRET_KEY` in env and never touches the DB.
