"""Tests for config/strategy_config.py — load_strategy_config"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading.config.strategy_config import (
    StrategyConfig,
    StrategyParams,
    load_strategy_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(data: dict, tmp_path: Path) -> Path:
    p = tmp_path / "strategy_config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


_MINIMAL = {
    "strategy": {"id": "ema_crossover", "params": {"fast": 9, "slow": 21}},
    "feature_engine": {"id": "technical", "params": {"ema_spans": [9, 21]}},
    "strategies": {},
    "hyperparam_search": {
        "strategy_id": "ema_crossover",
        "symbols": ["INFY"],
        "interval": "15min",
        "equity": 10000.0,
        "months": 6,
        "end_date": "2026-01-01",
        "grids": {
            "ema_crossover": {
                "fast_periods": [5, 9],
                "slow_periods": [21, 50],
                "atr_multipliers": [1.5, 2.0],
            }
        },
    },
}


# ---------------------------------------------------------------------------
# load_strategy_config
# ---------------------------------------------------------------------------


def test_load_strategy_config_returns_strategy_config(tmp_path: Path) -> None:
    p = _write_config(_MINIMAL, tmp_path)
    cfg = load_strategy_config(p)
    assert isinstance(cfg, StrategyConfig)


def test_load_strategy_config_active_strategy_id(tmp_path: Path) -> None:
    p = _write_config(_MINIMAL, tmp_path)
    cfg = load_strategy_config(p)
    assert cfg.strategy.id == "ema_crossover"


def test_load_strategy_config_strategy_params(tmp_path: Path) -> None:
    p = _write_config(_MINIMAL, tmp_path)
    cfg = load_strategy_config(p)
    assert cfg.strategy.params["fast"] == 9
    assert cfg.strategy.params["slow"] == 21


def test_load_strategy_config_ema_spans_coerced_to_tuple(tmp_path: Path) -> None:
    p = _write_config(_MINIMAL, tmp_path)
    cfg = load_strategy_config(p)
    assert isinstance(cfg.feature_engine.params["ema_spans"], tuple)


def test_load_strategy_config_hyperparam_search(tmp_path: Path) -> None:
    p = _write_config(_MINIMAL, tmp_path)
    cfg = load_strategy_config(p)
    hp = cfg.hyperparam_search
    assert hp.active_strategy == "ema_crossover"
    assert hp.symbols == ["INFY"]
    assert hp.interval == "15min"
    assert hp.equity == 10000.0
    assert hp.months == 6
    assert hp.end_date == "2026-01-01"


def test_load_strategy_config_grid_accessors(tmp_path: Path) -> None:
    p = _write_config(_MINIMAL, tmp_path)
    cfg = load_strategy_config(p)
    hp = cfg.hyperparam_search
    assert hp.fast_periods == [5, 9]
    assert hp.slow_periods == [21, 50]
    assert hp.atr_multipliers == [1.5, 2.0]


def test_load_strategy_config_named_strategies(tmp_path: Path) -> None:
    data = dict(_MINIMAL)
    data["strategies"] = {
        "ema_crossover": {"params": {"fast": 9, "slow": 21}, "feature_engine_params": {}},
    }
    p = _write_config(data, tmp_path)
    cfg = load_strategy_config(p)
    assert "ema_crossover" in cfg.strategies
    assert cfg.strategies["ema_crossover"].params["fast"] == 9


def test_load_strategy_config_file_not_found_raises() -> None:
    with pytest.raises(FileNotFoundError, match="strategy_config.json not found"):
        load_strategy_config(Path("/nonexistent/path/strategy_config.json"))


def test_load_strategy_config_months_none(tmp_path: Path) -> None:
    data = dict(_MINIMAL)
    data["hyperparam_search"] = dict(data["hyperparam_search"])
    data["hyperparam_search"]["months"] = None
    p = _write_config(data, tmp_path)
    cfg = load_strategy_config(p)
    assert cfg.hyperparam_search.months is None


def test_load_strategy_config_empty_grid_accessors(tmp_path: Path) -> None:
    data = dict(_MINIMAL)
    data["hyperparam_search"] = dict(data["hyperparam_search"])
    data["hyperparam_search"]["grids"] = {}  # no grid for active strategy
    p = _write_config(data, tmp_path)
    cfg = load_strategy_config(p)
    hp = cfg.hyperparam_search
    assert hp.fast_periods == []
    assert hp.slow_periods == []
    assert hp.atr_multipliers == []


def test_strategy_params_defaults() -> None:
    sp = StrategyParams(id="x", params={})
    assert sp.feature_engine_params == {}
