from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dependency_injector import containers, providers
from trading_risk_sdk.gates.circuit_breaker import CircuitBreakerGate
from trading_risk_sdk.gates.daily_loss import DailyLossGate
from trading_risk_sdk.gates.duplicate_position import DuplicatePositionGate
from trading_risk_sdk.gates.time_cutoff import TimeCutoffGate
from trading_risk_sdk.policy import RiskGate
from trading_strategy_sdk.base import Strategy
from trading_strategy_sdk.dpo_mean_reversion import DpoMeanReversionStrategy
from trading_strategy_sdk.ema_crossover import EmaCrossoverStrategy
from trading_strategy_sdk.linreg_trend import LinRegTrendStrategy
from trading_strategy_sdk.opening_range_breakout import OpeningRangeBreakoutStrategy
from trading_strategy_sdk.rsi_mean_reversion import RsiMeanReversionStrategy
from trading_strategy_sdk.squeeze_breakout import SqueezeBreakoutStrategy
from trading_strategy_sdk.vwap_reversion import VwapReversionStrategy


class AlgoStepContainer(containers.DeclarativeContainer):
    """
    Config-driven, swappable pipeline steps: pick a strategy or risk gate by
    string id (from AlgoSettings.strategy_id / GateConfig.gate_id) and
    construct it with per-step params from config.

    Each id maps to a Factory provider (a fresh instance per call, matching
    that neither strategies nor gates are process-wide singletons -- each
    algo gets its own). Shared infra deps (clock, stores) can be added as
    extra Factory kwargs here later without changing how callers select a
    step by id.

    Registered ids must match trading_strategy_sdk.factory's _STRATEGIES and
    trading_risk_sdk.registry's _GATES exactly -- both packages remain the
    source of truth for "what strategies/gates exist"; this container is
    only the DI-facing selection mechanism on top of them.
    """

    strategies: providers.Aggregate[Strategy] = providers.Aggregate(
        ema_crossover=providers.Factory(EmaCrossoverStrategy),
        rsi_mean_reversion=providers.Factory(RsiMeanReversionStrategy),
        opening_range_breakout=providers.Factory(OpeningRangeBreakoutStrategy),
        vwap_reversion=providers.Factory(VwapReversionStrategy),
        linreg_trend=providers.Factory(LinRegTrendStrategy),
        dpo_mean_reversion=providers.Factory(DpoMeanReversionStrategy),
        squeeze_breakout=providers.Factory(SqueezeBreakoutStrategy),
    )

    gates: providers.Aggregate[RiskGate] = providers.Aggregate(
        time_cutoff=providers.Factory(TimeCutoffGate),
        circuit_breaker=providers.Factory(CircuitBreakerGate),
        daily_loss=providers.Factory(DailyLossGate),
        duplicate_position=providers.Factory(DuplicatePositionGate),
    )


@dataclass(frozen=True)
class AlgoSteps:
    """
    Plain, passable handle to AlgoStepContainer's two Aggregate providers.

    AlgoStepContainer itself is a DeclarativeContainer -- instantiating it
    (AlgoStepContainer()) produces a DynamicContainer, NOT an
    AlgoStepContainer instance, so it can't be typed/passed as a normal
    Dependency(instance_of=...) value across other containers. AlgoSteps
    wraps just the two callables (`strategies(id, **params)`,
    `gates(id, **params)`) that callers actually need, so the rest of the
    app can depend on a plain, ordinary object instead of container
    instantiation internals.
    """

    strategies: providers.Provider[Strategy]
    gates: providers.Provider[RiskGate]

    def strategy(self, strategy_id: str, params: dict[str, Any] | None = None) -> Strategy:
        return self.strategies(strategy_id, **(params or {}))

    def gate(self, gate_id: str, params: dict[str, Any] | None = None) -> RiskGate:
        return self.gates(gate_id, **(params or {}))


def build_algo_steps() -> AlgoSteps:
    container = AlgoStepContainer()
    return AlgoSteps(strategies=container.strategies, gates=container.gates)
