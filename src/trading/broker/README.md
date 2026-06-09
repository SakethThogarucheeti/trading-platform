# broker

Broker abstraction layer — defines the `Broker` and `BrokerStream` ABCs and provides two concrete implementations: the live Zerodha adapter and an in-process paper broker for testing and simulation.

## Layout

```
broker/
├── api/
│   ├── __init__.py       Re-exports: Broker, BrokerStream, AbstractPriceStore, Tick, BrokerConfig
│   └── interfaces.py     AbstractPriceStore protocol
├── service/
│   ├── broker.py         Broker ABC — place_order, cancel_order, get_positions, fetch_candles
│   ├── broker_stream.py  BrokerStream ABC — start/stop WebSocket tick feed
│   ├── paper_broker.py   PaperBroker + PriceStore (in-memory fill simulation)
│   └── zerodha/
│       ├── broker.py     ZerodhaBroker (live implementation)
│       ├── kite_client.py KiteConnect HTTP wrapper
│       ├── models.py     KiteOrder, KitePosition typed dicts
│       └── stream.py     ZerodhaStream (KiteTicker WebSocket adapter)
├── storage/
│   └── models.py         BrokerToken ORM model (encrypted credential storage)
└── di/
    └── providers.py      BrokerProvider — selects live vs paper based on config
```

## Key abstractions

**`Broker`** (service/broker.py) — the interface every order-routing component depends on. Concrete impls: `ZerodhaBroker` (live) and `PaperBroker` (simulation).

**`BrokerStream`** (service/broker_stream.py) — WebSocket tick feed. `ZerodhaStream` wraps KiteTicker; `PaperBroker` doubles as a synthetic stream for backtests.

**`AbstractPriceStore`** (api/interfaces.py) — a `Protocol` that gives components read access to the latest tick price. Implemented by `PriceStore` (in-memory dict) which is updated by `TickSubscriber` from Redis.

## Credential storage

`BrokerToken` stores the Zerodha access token encrypted with `pgp_sym_encrypt`. The encryption key comes from `TOKEN_SECRET_KEY` in env and never touches the DB.
