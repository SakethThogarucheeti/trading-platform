from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from kiteconnect import KiteTicker  # type: ignore[import-untyped]

from trading.broker.base.broker_stream import BrokerStream
from trading.broker.types import Tick
from trading.broker.zerodha.kite_client import KiteClient

logger = logging.getLogger(__name__)


def _parse_tick(raw: Any) -> Tick:
    # raw is an untyped dict from KiteTicker — Any is unavoidable here
    # since kiteconnect has no type stubs and the payload schema is undocumented.
    return Tick(
        instrument_token=int(raw["instrument_token"]),
        last_price=float(raw["last_price"]),
        volume_traded=int(raw.get("volume_traded", raw.get("volume", 0))),
    )


class ZerodhaStream(BrokerStream):
    """
    Thin async wrapper around KiteTicker (WebSocket feed).

    KiteTicker runs in its own background thread; all callbacks arrive
    on that thread. The caller (KiteIngestor) is responsible for bridging
    callbacks to the asyncio event loop.
    """

    def __init__(self, client: KiteClient) -> None:
        self._client = client
        self._ticker: Any = None  # KiteTicker (untyped third-party)
        self._on_connect_cb: Callable[[], None] | None = None
        self._on_ticks_cb: Callable[[list[Tick]], None] | None = None
        self._on_disconnect_cb: Callable[[int, str], None] | None = None

    def set_on_connect(self, callback: Callable[[], None]) -> None:
        self._on_connect_cb = callback

    def set_on_ticks(self, callback: Callable[[list[Tick]], None]) -> None:
        self._on_ticks_cb = callback

    def set_on_disconnect(self, callback: Callable[[int, str], None]) -> None:
        self._on_disconnect_cb = callback

    async def connect(self) -> None:
        """Create and start KiteTicker in background thread (non-blocking)."""
        api_key = self._client._kite.api_key  # type: ignore[attr-defined]
        access_token = self._client._kite.access_token  # type: ignore[attr-defined]

        self._ticker = KiteTicker(api_key, access_token)  # type: ignore[no-untyped-call]

        def _on_connect(ws: object, response: object) -> None:
            if self._on_connect_cb:
                self._on_connect_cb()

        def _on_ticks(ws: object, ticks: list[Any]) -> None:
            if self._on_ticks_cb:
                self._on_ticks_cb([_parse_tick(t) for t in ticks])

        def _on_close(ws: object, code: int, reason: str) -> None:
            if self._on_disconnect_cb:
                self._on_disconnect_cb(code, reason)

        self._ticker.on_connect = _on_connect  # type: ignore[attr-defined]
        self._ticker.on_ticks = _on_ticks  # type: ignore[attr-defined]
        self._ticker.on_close = _on_close  # type: ignore[attr-defined]

        # threaded=True spawns a daemon thread and returns immediately.
        # The ticker runs its own Twisted reactor in that thread for the
        # lifetime of the WebSocket connection.
        self._ticker.connect(threaded=True)  # type: ignore[attr-defined]

    async def subscribe(self, tokens: list[int]) -> None:
        if self._ticker is None:
            raise RuntimeError("ZerodhaStream: not connected")
        from anyio import to_thread

        await to_thread.run_sync(self._ticker.subscribe, tokens)  # type: ignore[attr-defined]
        await to_thread.run_sync(lambda: self._ticker.set_mode(self._ticker.MODE_FULL, tokens))  # type: ignore[attr-defined]

    async def close(self) -> None:
        if self._ticker is not None:
            from anyio import to_thread

            await to_thread.run_sync(self._ticker.close)  # type: ignore[attr-defined]
            self._ticker = None

    async def reconnect(self) -> None:
        """Close the current KiteTicker and open a fresh one with the latest token."""
        await self.close()
        await self.connect()
