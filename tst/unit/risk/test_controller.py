"""Tests for risk/controller.py — re-export shim."""

from __future__ import annotations


def test_risk_controller_exports_risk_config() -> None:
    from trading.risk.controller import RiskConfig

    cfg = RiskConfig(equity=50_000.0)
    assert cfg.equity == 50_000.0


def test_risk_controller_exports_risk_registry() -> None:
    from trading.risk.controller import RiskRegistry

    assert RiskRegistry is not None


def test_risk_controller_all_contains_expected_names() -> None:
    import trading.risk.controller as mod

    assert "RiskConfig" in mod.__all__
    assert "RiskRegistry" in mod.__all__
