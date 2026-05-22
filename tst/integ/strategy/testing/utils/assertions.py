from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from testing.backtesting.report import BacktestReport
    from testing.monte_carlo.report import MonteCarloReport

# ---------------------------------------------------------------------------
# Domain assertions for use in pytest tests and TestHarness evaluation.
#
# These functions raise AssertionError with a descriptive message on failure.
# They intentionally do NOT catch exceptions — let pytest report the traceback.
# ---------------------------------------------------------------------------


def assert_sharpe_above(report: BacktestReport, threshold: float) -> None:
    """Assert that the backtest Sharpe ratio exceeds *threshold*."""
    assert report.sharpe_ratio >= threshold, (
        f"Sharpe ratio {report.sharpe_ratio:.4f} is below threshold {threshold:.4f} "
        f"(session_id={report.session_id})"
    )


def assert_max_drawdown_below(report: BacktestReport, threshold: float) -> None:
    """
    Assert that the maximum drawdown is below *threshold*.

    *threshold* is a fraction (0.20 = 20%). The report value is also a
    fraction in [0.0, 1.0].
    """
    assert report.max_drawdown <= threshold, (
        f"Max drawdown {report.max_drawdown:.2%} exceeds threshold {threshold:.2%} "
        f"(session_id={report.session_id})"
    )


def assert_win_rate_above(report: BacktestReport, threshold: float) -> None:
    """Assert that the win rate (fraction of profitable trades) exceeds *threshold*."""
    assert report.win_rate >= threshold, (
        f"Win rate {report.win_rate:.2%} is below threshold {threshold:.2%} "
        f"(session_id={report.session_id})"
    )


def assert_no_ruin(mc_report: MonteCarloReport, max_prob: float = 0.01) -> None:
    """
    Assert that the Monte Carlo ruin probability does not exceed *max_prob*.

    Ruin is defined as losing more than 50% of initial equity in a trial.
    Default threshold is 1%.
    """
    assert mc_report.probability_of_ruin <= max_prob, (
        f"Ruin probability {mc_report.probability_of_ruin:.2%} exceeds "
        f"maximum allowed {max_prob:.2%} (session_id={mc_report.session_id})"
    )


def assert_pnl_positive(report: BacktestReport) -> None:
    """Assert that the backtest ended with a profit (final_equity > initial_equity)."""
    initial = report.config.initial_equity
    assert report.final_equity > initial, (
        f"Backtest ended at a loss: final_equity={report.final_equity:.2f} "
        f"< initial_equity={initial:.2f} (session_id={report.session_id})"
    )


def assert_total_trades_above(report: BacktestReport, min_trades: int) -> None:
    """Assert that the backtest generated at least *min_trades* completed trades."""
    assert report.total_trades >= min_trades, (
        f"Too few trades: {report.total_trades} < {min_trades} (session_id={report.session_id})"
    )
