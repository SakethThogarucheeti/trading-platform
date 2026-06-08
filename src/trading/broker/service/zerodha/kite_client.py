from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, cast

from kiteconnect import KiteConnect

from trading.broker.service.zerodha.models import (
    ZerodhaCandle,
    ZerodhaInstrument,
    ZerodhaProfile,
    ZerodhaSession,
)


class _KiteProtocol(Protocol):
    def login_url(self) -> str: ...

    def generate_session(
        self,
        request_token: str,
        api_secret: str,
    ) -> Any: ...

    def set_access_token(self, token: str) -> None: ...

    def profile(self) -> ZerodhaProfile: ...

    def instruments(
        self,
        exchange: str,
    ) -> Sequence[ZerodhaInstrument]: ...

    def historical_data(
        self,
        instrument_token: int,
        from_date: datetime,
        to_date: datetime,
        interval: str,
        continuous: bool = False,
        oi: bool = False,
    ) -> Sequence[ZerodhaCandle]: ...

    def place_order(self, **kwargs: Any) -> str: ...


class KiteClient:
    def __init__(self, api_key: str) -> None:
        self._kite: _KiteProtocol = cast(
            _KiteProtocol,
            KiteConnect(api_key=api_key),
        )

    def login_url(self) -> str:
        return self._kite.login_url()

    def generate_session(
        self,
        request_token: str,
        api_secret: str,
    ) -> ZerodhaSession:
        raw: Any = self._kite.generate_session(
            request_token,
            api_secret=api_secret,
        )

        if not isinstance(raw, dict):
            raise TypeError("Invalid Zerodha response")

        return cast(ZerodhaSession, raw)

    def set_access_token(self, token: str) -> None:
        self._kite.set_access_token(token)

    def profile(self) -> ZerodhaProfile:
        return self._kite.profile()

    def instruments(self, exchange: str) -> Sequence[ZerodhaInstrument]:
        return self._kite.instruments(exchange)

    def historical_data(
        self,
        token: int,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> Sequence[ZerodhaCandle]:
        return self._kite.historical_data(
            token,
            start,
            end,
            interval,
        )

    def place_order(self, **kwargs: Any) -> str:
        return str(self._kite.place_order(**kwargs))
