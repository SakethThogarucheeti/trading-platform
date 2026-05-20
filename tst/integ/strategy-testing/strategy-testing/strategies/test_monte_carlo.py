"""
Monte Carlo simulation tests.

Verifies distribution properties, ruin probability, and report generation.
No external dependencies — purely in-memory computation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from testing.backtesting.portfolio import TradeRecord
from testing.monte_carlo.report import MonteCarloConfig
from testing.monte_carlo.simulator import MonteCarloSimulator


def _trades(
    n: int = 50, win_rate: float = 0.6, avg_win: float = 200.0, avg_loss: float = 100.0
) -> list[TradeRecord]:
    """Generate a synthetic list of trades with the given win rate."""
    import random

    rng = random.Random(42)
    now = datetime.now(UTC)
    trades = []
    for _i in range(n):
        won = rng.random() < win_rate
        pnl = avg_win if won else -avg_loss
        trades.append(
            TradeRecord(
                symbol="INFY",
                side="BUY",
                qty=10,
                entry_price=1000.0,
                exit_price=1000.0 + (pnl / 10),
                pnl=pnl,
                entry_time=now,
                exit_time=now,
            )
        )
    return trades


@pytest.mark.asyncio
async def test_monte_carlo_different_seeds_produce_different_results(tmp_path):
    """Different seeds must produce different raw trial sequences."""
    trades = _trades(n=100)

    config1 = MonteCarloConfig(n_trials=200, method="bootstrap", seed=1, session_id="mc1")
    config2 = MonteCarloConfig(n_trials=200, method="bootstrap", seed=999, session_id="mc2")

    sim1 = MonteCarloSimulator(config=config1, trades=trades, results_dir=tmp_path)
    sim2 = MonteCarloSimulator(config=config2, trades=trades, results_dir=tmp_path)

    report1 = await sim1.run()
    report2 = await sim2.run()

    rets1 = report1.return_distribution.to_list()
    rets2 = report2.return_distribution.to_list()
    diffs = sum(1 for a, b in zip(rets1, rets2, strict=False) if abs(a - b) > 1e-10)
    assert diffs > 0, (
        "Different seeds should produce different trial sequences; "
        f"found {diffs} differing trials out of {len(rets1)}"
    )


@pytest.mark.asyncio
async def test_ruin_probability_low_for_profitable_strategy(tmp_path):
    """A strategy with 60% win rate and 2:1 RR should have near-zero ruin probability."""
    trades = _trades(n=100, win_rate=0.6, avg_win=200.0, avg_loss=100.0)

    config = MonteCarloConfig(
        n_trials=500, method="bootstrap", seed=42, initial_equity=100_000.0, session_id="mc_ruin"
    )
    sim = MonteCarloSimulator(config=config, trades=trades, results_dir=tmp_path)
    report = await sim.run()

    assert report.probability_of_ruin < 0.05, (
        f"Profitable strategy should have low ruin probability, got {report.probability_of_ruin:.2%}"
    )


@pytest.mark.asyncio
async def test_ruin_probability_high_for_losing_strategy(tmp_path):
    """A strategy with 30% win rate should have higher ruin probability."""
    trades = _trades(n=100, win_rate=0.3, avg_win=100.0, avg_loss=200.0)

    config = MonteCarloConfig(
        n_trials=500, method="bootstrap", seed=42, initial_equity=10_000.0, session_id="mc_losing"
    )
    sim = MonteCarloSimulator(config=config, trades=trades, results_dir=tmp_path)
    report = await sim.run()

    assert report.probability_of_ruin > 0.1, (
        "Losing strategy should have significant ruin probability,"
        f" got {report.probability_of_ruin:.2%}"
    )


@pytest.mark.asyncio
async def test_html_report_contains_plotly(tmp_path):
    """The HTML report must embed Plotly (no external CDN references for key charts)."""
    trades = _trades(n=30)

    config = MonteCarloConfig(n_trials=20, method="shuffle", seed=0, session_id="mc_html")
    sim = MonteCarloSimulator(config=config, trades=trades, results_dir=tmp_path)
    report = await sim.run()

    html = report.to_html()
    assert "plotly" in html.lower(), "HTML must contain Plotly"
    assert "<html" in html.lower(), "Must be a valid HTML document"


@pytest.mark.asyncio
async def test_same_seed_reproducible(tmp_path):
    """Same seed must produce identical results."""
    trades = _trades(n=40)

    config_a = MonteCarloConfig(n_trials=100, method="bootstrap", seed=99, session_id="mc_a")
    config_b = MonteCarloConfig(n_trials=100, method="bootstrap", seed=99, session_id="mc_b")

    sim_a = MonteCarloSimulator(config=config_a, trades=trades, results_dir=tmp_path)
    sim_b = MonteCarloSimulator(config=config_b, trades=trades, results_dir=tmp_path)

    r_a = await sim_a.run()
    r_b = await sim_b.run()

    assert r_a.probability_of_ruin == r_b.probability_of_ruin
    assert r_a.percentile_5_return == pytest.approx(r_b.percentile_5_return)
