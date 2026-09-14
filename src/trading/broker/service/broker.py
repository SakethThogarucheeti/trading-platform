from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import polars as pl

from trading.core.schemas import OrderType, Side


class Broker(ABC):
    """Abstract base for all broker implementations."""

    @abstractmethod
    def get_instruments(self) -> pl.DataFrame:
        pass

    @abstractmethod
    def get_ohlc(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> pl.DataFrame:
        pass

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: Side,
        qty: int,
        order_type: OrderType,
        limit_price: float | None = None,
        instrument_type: str = "EQUITY",
        tick_log_id: int = 0,
        client_tag: str | None = None,
    ) -> str:
        """
        Place an order and return the broker-assigned order ID.

        Parameters
        ----------
        symbol:
            Instrument trading symbol (e.g. "INFY").
        side:
            BUY or SELL.
        qty:
            Number of shares / contracts.
        order_type:
            MARKET, LIMIT, SL, or SL_M.
        limit_price:
            Required for LIMIT and SL orders; None for MARKET/SL_M.
        client_tag:
            Our own locally-generated id, echoed back by the broker on every
            order/postback it returns for it (see ZerodhaBroker) -- lets us find
            this order again by tag if we never captured the broker's real
            order id (e.g. the placement call timed out).

        Returns
        -------
        str
            Broker-assigned order ID (e.g. Zerodha's kite_order_id).
        """
        ...
