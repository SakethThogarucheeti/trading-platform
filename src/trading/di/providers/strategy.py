from __future__ import annotations

from typing import Any

from trading.core.clock import Clock
from trading.strategy.base import Strategy
from trading.strategy.factory import create_strategy


def make_strategy(
    strategy_id: str,
    params: dict[str, Any] | None = None,
    clock: Clock | None = None,
) -> Strategy:
    """
    Instantiate the strategy identified by *strategy_id*.

    Parameters
    ----------
    strategy_id:
        Key in ``_STRATEGIES`` in ``trading/strategy/factory.py`` (e.g. ``"ema_crossover"``).
    params:
        Optional keyword arguments forwarded to the strategy constructor.
    clock:
        Optional clock injected into strategies that accept one (e.g.
        ``VwapReversionStrategy``). Pass a ``SimulatedClock`` during backtesting.

    To add a new strategy: create a module under ``trading/strategy/``, subclass
    ``Strategy``, and add an entry to ``_STRATEGIES`` in ``trading/strategy/factory.py``.
    """
    return create_strategy(strategy_id, params=params, clock=clock)
