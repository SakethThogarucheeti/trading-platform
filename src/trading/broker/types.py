from __future__ import annotations

from typing import TypedDict


class Tick(TypedDict, total=False):
    """
    Normalised tick dict produced by ZerodhaStream from a raw KiteTicker payload.

    ``total=False`` because Kite only guarantees ``instrument_token`` and
    ``last_price`` across all quote modes; ``volume_traded`` is absent in
    LTP mode and present in QUOTE/FULL mode.
    """

    instrument_token: int
    last_price: float
    volume_traded: int
