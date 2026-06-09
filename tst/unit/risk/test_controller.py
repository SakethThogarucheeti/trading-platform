"""Tests for risk/service/filter.py — RiskConfig and RiskFilter."""

from __future__ import annotations


def test_risk_filter_exports_risk_config() -> None:
    from trading.risk.service.filter import RiskConfig

    cfg = RiskConfig(equity=50_000.0)
    assert cfg.equity == 50_000.0


def test_risk_filter_class_is_accessible() -> None:
    from trading.risk.service.filter import RiskFilter

    assert RiskFilter is not None


def test_risk_api_exports_expected_names() -> None:
    import trading.risk.api as mod

    assert hasattr(mod, "RiskFilter")
    assert hasattr(mod, "ValidatedOrderEvent")
