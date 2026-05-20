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

        Returns
        -------
        str
            Broker-assigned order ID (e.g. Zerodha's kite_order_id).
        """
        ...
