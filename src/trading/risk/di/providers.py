from __future__ import annotations

from dishka import Provider, Scope, provide  # type: ignore[import-untyped]

from trading.risk.service.filter import RiskConfig, RiskFilter
from trading.risk.service.policy import RiskGate


class RiskProvider(Provider):
    """Wires risk module internals. Gates and RiskFilter are assembled by trading.app."""

    scope = Scope.APP
    # The full RiskFilter wiring (gates list, store deps) is done in trading.app
    # since it requires cross-module DI resolution.
