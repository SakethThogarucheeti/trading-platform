"""trading.core.clock is a re-export shim over trading_types.clock.

Full behavioral coverage lives in trading-types' own test suite; this just
confirms the shim resolves to the real implementation.
"""

from __future__ import annotations

from trading.core.clock import SYSTEM_CLOCK, Clock, SimulatedClock, SystemClock
from trading_types.clock import SYSTEM_CLOCK as _REAL_SYSTEM_CLOCK
from trading_types.clock import Clock as _RealClock
from trading_types.clock import SimulatedClock as _RealSimulatedClock
from trading_types.clock import SystemClock as _RealSystemClock


def test_shim_reexports_match_trading_types() -> None:
    assert Clock is _RealClock
    assert SystemClock is _RealSystemClock
    assert SimulatedClock is _RealSimulatedClock
    assert SYSTEM_CLOCK is _REAL_SYSTEM_CLOCK
