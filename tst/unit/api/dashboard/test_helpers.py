"""Tests for api/routers/_helpers.py's shared DecisionLog condition builder."""

from __future__ import annotations

from datetime import UTC, datetime

from trading.api.routers._helpers import decision_log_base_conditions
from trading.core.clock import SimulatedClock
from trading.core.models import DecisionLog

_NOW = datetime(2026, 1, 6, 9, 30, tzinfo=UTC)


def _clock() -> SimulatedClock:
    clock = SimulatedClock()
    clock.advance(_NOW)
    return clock


def test_decision_log_base_conditions_no_session_no_algo():
    """No session_id/algo_name -> extra condition, today's-date filter, session IS NULL."""
    extra_cond = DecisionLog.id > 5
    conditions = decision_log_base_conditions(_clock(), "", "", extra_cond)

    assert len(conditions) == 3
    assert conditions[0].compare(extra_cond)
    assert conditions[2].compare(DecisionLog.session_id.is_(None))


def test_decision_log_base_conditions_with_session_no_algo():
    """A session_id scopes to that session; algo_name absent -> no algo filter appended."""
    extra_cond = DecisionLog.id > 5
    conditions = decision_log_base_conditions(_clock(), "sess-1", "", extra_cond)

    assert len(conditions) == 3
    assert conditions[2].compare(DecisionLog.session_id == "sess-1")


def test_decision_log_base_conditions_with_algo_name():
    """algo_name present -> a 4th condition is appended, after session scoping."""
    extra_cond = DecisionLog.step.in_(["SIGNAL_GENERATED"])
    conditions = decision_log_base_conditions(_clock(), "sess-1", "ema_crossover", extra_cond)

    assert len(conditions) == 4
    assert conditions[0].compare(extra_cond)
    assert conditions[2].compare(DecisionLog.session_id == "sess-1")
    assert conditions[3].compare(DecisionLog.algo_name == "ema_crossover")


def test_decision_log_base_conditions_extra_ordering_preserved():
    """The route-specific extra condition must come first, matching the pre-refactor
    ordering in market.py's get_signals / stream.py's decisions_stream."""
    extra_cond = DecisionLog.id > 99
    conditions = decision_log_base_conditions(_clock(), "", "", extra_cond)

    assert conditions[0].compare(extra_cond)
