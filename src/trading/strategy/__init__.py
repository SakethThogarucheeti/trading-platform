"""
Strategy package.

Public API
----------
    from trading.strategy import Strategy, Signal
    from trading.strategy.factory import get_strategy, create_strategy, registered_strategies

    # Look up a strategy class by ID
    cls = get_strategy("ema_crossover")

    # Instantiate with optional params and clock
    inst = create_strategy("ema_crossover", params={"fast": 5, "slow": 13})

    # Inspect all registered strategies
    print(registered_strategies())

Built-in strategies:
    "ema_crossover"          EmaCrossoverStrategy
    "rsi_mean_reversion"     RsiMeanReversionStrategy
    "vwap_reversion"         VwapReversionStrategy
    "opening_range_breakout" OpeningRangeBreakoutStrategy
"""

from trading.strategy.base import Signal, Strategy

__all__ = ["Signal", "Strategy"]
