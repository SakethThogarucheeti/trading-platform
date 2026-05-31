from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from trading.core.clock import Clock
from trading.core.schemas import Side
from trading.storage.cache.backend import ValueCache
from trading.storage.cache.base import BaseCacher


class PnlCacher(BaseCacher[float]):
    """
    Manages the daily realized PnL value shared between:
      - OrderExecutor (writer): calls increment_sync() on every fill
      - RiskFilter (reader): calls get_or_set() to enforce the daily loss limit

    Key schema: rf:pnl:{YYYY-MM-DD}
    TTL: seconds remaining until midnight IST + 1 hour grace.
    """

    def __init__(self, cache: ValueCache, clock: Clock) -> None:
        super().__init__(cache)
        self._clock = clock

    def make_key(self, for_date: date) -> str:  # type: ignore[override]
        return f"rf:pnl:{for_date.isoformat()}"

    def default_ttl(self) -> int:
        now = self._clock.now()
        tomorrow = self._clock.today() + timedelta(days=1)
        midnight = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=self._clock.tz).astimezone(UTC)
        return int((midnight - now).total_seconds()) + 3600

    def increment_sync(self, for_date: date, side: Side, avg_price: float, qty: int) -> None:
        """Synchronously update PnL after a fill. Writes to memory only — no Redis I/O."""
        key = self.make_key(for_date)
        current = float(self._cache.get_sync(key) or 0.0)
        sign = 1.0 if side == Side.SELL else -1.0
        self._cache.set_sync(key, current + sign * avg_price * qty)
