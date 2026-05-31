"""
FillWebhookProcessor — parse and validate Zerodha postback payloads.

Separates payload parsing from the FastAPI handler so the normalization
logic can be tested without a running HTTP server or OrderExecutor.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FillPayload:
    """Normalized fields extracted from a Zerodha order-update postback."""

    kite_order_id: str
    avg_price: float
    filled_qty: int
    symbol: str
    instrument_type: str
    side: str
    tick_log_id: int


class WebhookValidationError(ValueError):
    """Raised when the postback payload is missing required fields or has bad values."""


def parse_fill_payload(raw: dict[str, object]) -> FillPayload | None:
    """
    Parse a raw Zerodha postback dict into a FillPayload.

    Returns None if the order status is not COMPLETE (caller should skip silently).
    Raises WebhookValidationError if status is COMPLETE but required fields are absent
    or invalid.
    """
    status = str(raw.get("status", ""))
    if status != "COMPLETE":
        return None

    kite_order_id = str(raw.get("order_id", ""))
    symbol = str(raw.get("tradingsymbol", ""))
    instrument_type = str(raw.get("instrument_type", "EQUITY"))
    side = str(raw.get("transaction_type", "BUY"))
    tick_log_id = int(str(raw.get("tick_log_id", 0)))

    try:
        avg_price = float(str(raw.get("average_price", 0)))
        filled_qty = int(str(raw.get("filled_quantity", 0)))
    except (TypeError, ValueError) as exc:
        raise WebhookValidationError(f"Non-numeric fill field: {exc}") from exc

    if not kite_order_id or not symbol or filled_qty <= 0 or avg_price <= 0:
        raise WebhookValidationError(
            f"Missing required fill fields: order_id={kite_order_id!r} "
            f"symbol={symbol!r} filled_qty={filled_qty} avg_price={avg_price}"
        )

    return FillPayload(
        kite_order_id=kite_order_id,
        avg_price=avg_price,
        filled_qty=filled_qty,
        symbol=symbol,
        instrument_type=instrument_type,
        side=side,
        tick_log_id=tick_log_id,
    )
