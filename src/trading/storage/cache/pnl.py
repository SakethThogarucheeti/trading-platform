from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from trading.core.schemas import Side
from trading.storage.cache.base import BaseCacher
from trading.storage.cache.backend import ValueCache


class PnlCacher(BaseCacher[float]):
    """
    Manages the daily realized PnL value shared between:
      - OrderExecutor (writer): calls increment_sync() on every fill
      - RiskFilter (reader): calls get_or_set() to enforce the daily loss limit

    Key schema: rf:pnl:{YYYY-MM-DD}
    TTL: seconds remaining until midnight UTC + 1 hour grace.
    """

    def __init__(self, cache: ValueCache) -> None:
        super().__init__(cache)

    def make_key(self, for_date: date) -> str:  # type: ignore[override]
        return f"rf:pnl:{for_date.isoformat()}"

    def default_ttl(self) -> int:
        now = datetime.now(UTC)
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return int((midnight - now).total_seconds()) + 3600

    def increment_sync(self, for_date: date, side: Side, avg_price: float, qty: int) -> None:
        """Synchronously update PnL after a fill. Writes to memory only — no Redis I/O."""
        key = self.make_key(for_date)
        current = float(self._cache.get_sync(key) or 0.0)
        sign = 1.0 if side == Side.SELL else -1.0
        self._cache.set_sync(key, current + sign * avg_price * qty)
