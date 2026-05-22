from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import polars as pl

from trading.core.schemas import Side

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """A completed round-trip trade (entry + exit)."""

    symbol: str
    side: str  # "BUY" | "SELL" (direction of the entry leg)
    qty: int
    entry_price: float
    exit_price: float
    pnl: float
    entry_time: datetime
    exit_time: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl": self.pnl,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
        }


@dataclass
class _OpenPosition:
    symbol: str
    side: Side
    qty: int
    entry_price: float
    entry_time: datetime


class EquityTracker:
    """
    Track equity curve and completed trades during a backtest.

    Call ``process_fill()`` directly for each executed order, then
    ``close_open_positions(last_price_map)`` after replay ends.

    Equity curve columns: ``date`` (datetime), ``equity`` (float).
    """

    def __init__(self, initial_equity: float) -> None:
        self._equity = initial_equity
        self._initial_equity = initial_equity
        self._open: dict[str, _OpenPosition] = {}  # symbol → open position
        self._trades: list[TradeRecord] = []
        self._equity_snapshots: list[tuple[datetime, float]] = []

    def process_fill(
        self,
        symbol: str,
        side: Side,
        qty: int,
        price: float,
        ts: datetime,
    ) -> None:
        """Process a fill directly (used when symbol/side context is known)."""
        self._process_fill(symbol, side, qty, price, ts)

    def _process_fill(
        self,
        symbol: str,
        side: Side,
        qty: int,
        price: float,
        ts: datetime,
    ) -> None:
        existing = self._open.get(symbol)

        if existing is None:
            # Opening a new position
            self._open[symbol] = _OpenPosition(
                symbol=symbol,
                side=side,
                qty=qty,
                entry_price=price,
                entry_time=ts,
            )
            logger.debug("EquityTracker: OPEN %s %s x%d @ %.4f", symbol, side.value, qty, price)
        else:
            # Closing (or partially closing) the position
            if existing.side == side:
                # Same direction — add to position
                total_qty = existing.qty + qty
                avg_price = (existing.entry_price * existing.qty + price * qty) / total_qty
                self._open[symbol] = _OpenPosition(
                    symbol=symbol,
                    side=side,
                    qty=total_qty,
                    entry_price=avg_price,
                    entry_time=existing.entry_time,
                )
            else:
                # Opposite direction — close position
                if side == Side.SELL:
                    pnl = (price - existing.entry_price) * min(qty, existing.qty)
                else:
                    pnl = (existing.entry_price - price) * min(qty, existing.qty)

                self._equity += pnl
                self.snapshot(ts)

                trade = TradeRecord(
                    symbol=symbol,
                    side=existing.side.value,
                    qty=min(qty, existing.qty),
                    entry_price=existing.entry_price,
                    exit_price=price,
                    pnl=pnl,
                    entry_time=existing.entry_time,
                    exit_time=ts,
                )
                self._trades.append(trade)
                logger.debug(
                    "EquityTracker: CLOSE %s pnl=%.2f equity=%.2f",
                    symbol,
                    pnl,
                    self._equity,
                )

                if qty >= existing.qty:
                    del self._open[symbol]
                else:
                    self._open[symbol] = _OpenPosition(
                        symbol=symbol,
                        side=existing.side,
                        qty=existing.qty - qty,
                        entry_price=existing.entry_price,
                        entry_time=existing.entry_time,
                    )

    def snapshot(self, ts: datetime) -> None:
        """Record current equity at timestamp *ts*."""
        self._equity_snapshots.append((ts, self._equity))

    def close_open_positions(self, last_prices: dict[str, float]) -> None:
        """
        Close all still-open positions at *last_prices* (end of backtest).

        Called by ``BacktestSession`` after the replay finishes.
        """
        now = datetime.now(UTC)
        for symbol, pos in list(self._open.items()):
            price = last_prices.get(symbol)
            if price is None:
                logger.warning(
                    "EquityTracker: no close price for %s — using entry price %.4f (zero PnL)",
                    symbol,
                    pos.entry_price,
                )
                price = pos.entry_price
            if pos.side == Side.BUY:
                pnl = (price - pos.entry_price) * pos.qty
            else:
                pnl = (pos.entry_price - price) * pos.qty

            self._equity += pnl
            self.snapshot(now)

            trade = TradeRecord(
                symbol=symbol,
                side=pos.side.value,
                qty=pos.qty,
                entry_price=pos.entry_price,
                exit_price=price,
                pnl=pnl,
                entry_time=pos.entry_time,
                exit_time=now,
            )
            self._trades.append(trade)
        self._open.clear()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_equity(self) -> float:
        return self._equity

    @property
    def initial_equity(self) -> float:
        return self._initial_equity

    @property
    def trades(self) -> list[TradeRecord]:
        return list(self._trades)

    @property
    def equity_curve(self) -> pl.DataFrame:
        """
        Return an equity curve DataFrame with columns ``[date, equity]``.

        Includes the initial equity point as the first row.
        """
        snapshots = [
            (
                self._equity_snapshots[0][0] if self._equity_snapshots else datetime.now(UTC),
                self._initial_equity,
            )
        ] + self._equity_snapshots
        dates = [s[0] for s in snapshots]
        equities = [s[1] for s in snapshots]
        return pl.DataFrame({"date": dates, "equity": equities})
