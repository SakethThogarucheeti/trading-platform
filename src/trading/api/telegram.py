from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from anyio import sleep

from trading.config.settings import Settings

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_RATE_LIMIT_SECS = 60


class TelegramAlerter:
    """
    Sends alert messages to a Telegram chat.

    Behaviour
    ---------
    - No-op if ``settings.telegram_bot_token`` is None.
    - Rate-limits per ``event_type``: a second alert of the same type
      within ``_RATE_LIMIT_SECS`` seconds is silently dropped.
    - Retries up to 3 times on HTTP 5xx or timeout.
    - Respects ``retry_after`` on HTTP 429 responses.
    """

    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.telegram_enabled
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id
        # event_type → last send timestamp
        self._last_sent: dict[str, float] = {}

    async def send_alert(self, message: str, event_type: str) -> None:
        """Send *message* to the configured Telegram chat."""
        if not self._enabled:
            return

        now = time.monotonic()
        if now - self._last_sent.get(event_type, 0) < _RATE_LIMIT_SECS:
            logger.debug("TelegramAlerter: rate-limited event_type=%s", event_type)
            return

        sent = await self._post(message)
        if sent:
            self._last_sent[event_type] = now

    async def _post(self, message: str, attempt: int = 1) -> bool:
        """POST message to Telegram API. Returns True on success."""
        if attempt > 3:
            logger.error("TelegramAlerter: gave up after 3 attempts")
            return False

        url = _TELEGRAM_API.format(token=self._token)
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "HTML",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)

            if response.status_code == 200:
                return True

            if response.status_code == 429:
                # Respect Telegram's retry-after header
                retry_after = int(response.headers.get("Retry-After", "5"))
                logger.warning(
                    "TelegramAlerter: rate limited by Telegram, waiting %ds", retry_after
                )
                await sleep(retry_after)
                return await self._post(message, attempt + 1)

            if response.status_code >= 500:
                logger.warning(
                    "TelegramAlerter: HTTP %d, retrying (attempt %d)",
                    response.status_code,
                    attempt,
                )
                await sleep(2**attempt)
                return await self._post(message, attempt + 1)

            logger.error(
                "TelegramAlerter: unexpected HTTP %d: %s",
                response.status_code,
                response.text[:200],
            )
            return False

        except httpx.TimeoutException:
            logger.warning("TelegramAlerter: timeout, retrying (attempt %d)", attempt)
            await sleep(2**attempt)
            return await self._post(message, attempt + 1)

        except Exception as exc:
            logger.error("TelegramAlerter: unexpected error — %s", exc)
            return False
