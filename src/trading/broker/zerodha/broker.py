from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import polars as pl

from trading.broker.base.broker import Broker
from trading.broker.zerodha.kite_client import KiteClient
from trading.core.schemas import OrderType, Side

_IST = timezone(timedelta(hours=5, minutes=30))

_ORDER_TIMEOUT_SECS = 10.0  # max time to wait for Zerodha REST API to respond

logger = logging.getLogger(__name__)

# Map our internal interval names → Zerodha API interval strings
_INTERVAL_MAP: dict[str, str] = {
    "1min": "minute",
    "3min": "3minute",
    "5min": "5minute",
    "10min": "10minute",
    "15min": "15minute",
    "30min": "30minute",
    "60min": "60minute",
    "day": "day",
}

_ORDER_TYPE_MAP: dict[OrderType, str] = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.SL: "SL",
    OrderType.SL_M: "SL-M",
}


class ZerodhaBroker(Broker):
    def __init__(
        self,
        client: KiteClient,
        exchange: str = "NSE",
        order_timeout_secs: float = _ORDER_TIMEOUT_SECS,
    ) -> None:
        self.client = client
        self.exchange = exchange
        self._order_timeout_secs = order_timeout_secs
        self._instruments: pl.DataFrame | None = None

    def get_instruments(self) -> pl.DataFrame:
        if self._instruments is None:
            data = self.client.instruments(self.exchange)
            self._instruments = pl.DataFrame(data)
        return self._instruments

    def _get_token(self, symbol: str) -> int:
        instruments: pl.DataFrame = self.get_instruments()

        # Pyright limitation: Polars dynamic API
        row: pl.DataFrame = instruments.filter(pl.col("tradingsymbol") == symbol)

        if row.height == 0:
            raise ValueError(f"Symbol not found: {symbol}")

        token_df: pl.DataFrame = row.select("instrument_token")
        return int(token_df.item())

    def get_ohlc(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> pl.DataFrame:
        token: int = self._get_token(symbol)

        # Kite historical_data expects naive IST datetimes; convert if UTC-aware.
        def _to_naive_ist(dt: datetime) -> datetime:
            if dt.tzinfo is not None:
                dt = dt.astimezone(_IST)
            return dt.replace(tzinfo=None)

        raw = self.client.historical_data(
            token,
            _to_naive_ist(start),
            _to_naive_ist(end),
            _INTERVAL_MAP.get(interval, interval),
        )

        df: pl.DataFrame = pl.DataFrame(raw)

        if df.is_empty():
            return df

        # Ensure date column is cast to UTC Datetime (Kite returns Python datetimes)
        if df["date"].dtype != pl.Datetime("us", "UTC"):
            df = df.with_columns(pl.col("date").cast(pl.Datetime("us", "UTC")))

        return df.sort("date")

    async def place_order(
        self,
        symbol: str,
        side: Side,
        qty: int,
        order_type: OrderType,
        limit_price: float | None = None,
        instrument_type: str = "EQUITY",
        tick_log_id: int = 0,
    ) -> str:
        """
        Place an order via Zerodha Kite REST API and return the kite_order_id.

        The Kite client is synchronous; we use asyncio.to_thread to avoid
        blocking the event loop.
        """
        kite_order_type = _ORDER_TYPE_MAP[order_type]
        transaction_type = "BUY" if side == Side.BUY else "SELL"

        def _place() -> str:
            return self.client.place_order(
                variety="regular",
                exchange=self.exchange,
                tradingsymbol=symbol,
                transaction_type=transaction_type,
                quantity=qty,
                product="MIS",
                order_type=kite_order_type,
                price=limit_price,
            )

        try:
            from anyio import fail_after, to_thread

            with fail_after(self._order_timeout_secs):
                order_id = await to_thread.run_sync(_place)
        except TimeoutError as err:
            raise RuntimeError(
                f"ZerodhaBroker: place_order timed out after {self._order_timeout_secs}s "
                f"for {transaction_type} {symbol} x{qty}"
            ) from err
        logger.info(
            "ZerodhaBroker: placed %s %s x%d → order_id=%s",
            transaction_type,
            symbol,
            qty,
            order_id,
        )
        return order_id
