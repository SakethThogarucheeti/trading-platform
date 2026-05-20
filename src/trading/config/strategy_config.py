"""
Loader for strategy_config.json.

Provides typed access to strategy params, feature engine params, and the
hyperparameter search grid. The config file is resolved relative to the
project root (two levels above this module's package directory).

Usage
-----
    from trading.config.strategy_config import load_strategy_config

    cfg = load_strategy_config()
    print(cfg.strategy.params)                    # active strategy params
    print(cfg.hyperparam_search.active_strategy)  # which strategy the grid runs
    print(cfg.hyperparam_search.grid)             # dict of grid param lists
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Resolve project root: src/trading/config/ → src/trading/ → src/ → project root
_PROJECT_ROOT = Path(__file__).parents[3]
_DEFAULT_PATH = _PROJECT_ROOT / "strategy_config.json"


@dataclass
class StrategyParams:
    id: str
    params: dict[str, Any]
    feature_engine_params: dict[str, Any] = field(default_factory=dict)  # type: ignore[assignment]


@dataclass
class HyperparamSearchConfig:
    active_strategy: str  # which strategy the grid targets
    symbols: list[str]
    interval: str
    equity: float
    months: int | None  # None → use all available data
    end_date: str  # ISO date string, e.g. "2026-04-17"
    grid: dict[str, list[Any]]  # param_name → list of values to sweep

    # Convenience accessors for EMA grid (read from grid dict)
    @property
    def fast_periods(self) -> list[int]:
        return [int(x) for x in self.grid.get("fast_periods", [])]

    @property
    def slow_periods(self) -> list[int]:
        return [int(x) for x in self.grid.get("slow_periods", [])]

    @property
    def atr_multipliers(self) -> list[float]:
        return [float(x) for x in self.grid.get("atr_multipliers", [])]


@dataclass
class StrategyConfig:
    # "strategy" block — the currently active single-strategy config
    strategy: StrategyParams
    feature_engine: StrategyParams
    # "strategies" block — named configs for all registered strategies
    strategies: dict[str, StrategyParams]
    hyperparam_search: HyperparamSearchConfig


def _coerce_ema_spans(params: dict[str, Any]) -> dict[str, Any]:
    """Convert ema_spans list → tuple (TechnicalFeatureEngine expects tuple)."""
    result = dict(params)
    if "ema_spans" in result and isinstance(result["ema_spans"], list):
        result["ema_spans"] = tuple(result["ema_spans"])  # type: ignore[arg-type]
    return result


def load_strategy_config(path: Path | None = None) -> StrategyConfig:
    """
    Load and parse strategy_config.json.

    Parameters
    ----------
    path:
        Path to the JSON file. Defaults to ``<project_root>/strategy_config.json``.

    Raises
    ------
    FileNotFoundError
        If the config file does not exist at the resolved path.
    """
    config_path = path or _DEFAULT_PATH

    if not config_path.exists():
        raise FileNotFoundError(
            f"strategy_config.json not found at {config_path}. "
            "Create it or pass an explicit path to load_strategy_config()."
        )

    logger.info("Loading strategy config from %s", config_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    # --- Active strategy (top-level "strategy" / "feature_engine" blocks) ---
    strategy_raw = raw.get("strategy", {})
    feature_raw = raw.get("feature_engine", {})

    active_strategy = StrategyParams(
        id=strategy_raw.get("id", "ema_crossover"),
        params=dict(strategy_raw.get("params", {})),
        feature_engine_params=_coerce_ema_spans(feature_raw.get("params", {})),
    )
    active_feature = StrategyParams(
        id=feature_raw.get("id", "technical"),
        params=_coerce_ema_spans(feature_raw.get("params", {})),
    )

    # --- Named strategies block ---
    strategies: dict[str, StrategyParams] = {}
    for name, s_raw in raw.get("strategies", {}).items():
        strategies[name] = StrategyParams(
            id=name,
            params=dict(s_raw.get("params", {})),
            feature_engine_params=_coerce_ema_spans(s_raw.get("feature_engine_params", {})),
        )

    # --- Hyperparam search ---
    hp_raw = raw.get("hyperparam_search", {})
    active_sid = hp_raw.get("strategy_id", active_strategy.id)
    grids = hp_raw.get("grids", {})
    active_grid = grids.get(active_sid, {})

    hp = HyperparamSearchConfig(
        active_strategy=active_sid,
        symbols=hp_raw.get("symbols", []),
        interval=hp_raw.get("interval", "15min"),
        equity=float(hp_raw.get("equity", 10_000.0)),
        months=hp_raw.get("months"),
        end_date=hp_raw.get("end_date", ""),
        grid=active_grid,
    )

    return StrategyConfig(
        strategy=active_strategy,
        feature_engine=active_feature,
        strategies=strategies,
        hyperparam_search=hp,
    )
