"""Tests for core/fifo.py — match_against.

Previously had zero direct test coverage (only exercised indirectly via
PositionAccountant/compute_pnl tests) despite being the shared FIFO-matching
primitive both the live accounting path and reporting call
(trading-platform#92).
"""

from __future__ import annotations

from decimal import Decimal

from trading.core.fifo import match_against


def test_full_match_drains_queue() -> None:
    queue: list[tuple[int, Decimal]] = [(10, Decimal("100"))]
    realized, remaining = match_against(queue, 10, Decimal("120"), sign=1)
    assert realized == Decimal("200")
    assert remaining == 0
    assert queue == []


def test_partial_match_leaves_queue_entry_reduced() -> None:
    queue: list[tuple[int, Decimal]] = [(10, Decimal("100"))]
    realized, remaining = match_against(queue, 4, Decimal("120"), sign=1)
    assert realized == Decimal("80")
    assert remaining == 0
    assert queue == [(6, Decimal("100"))]


def test_incoming_qty_exceeds_queue_returns_remaining() -> None:
    queue: list[tuple[int, Decimal]] = [(5, Decimal("100"))]
    realized, remaining = match_against(queue, 8, Decimal("120"), sign=1)
    assert realized == Decimal("100")  # 5 * (120 - 100)
    assert remaining == 3
    assert queue == []


def test_empty_queue_returns_zero_realized_and_full_remaining() -> None:
    queue: list[tuple[int, Decimal]] = []
    realized, remaining = match_against(queue, 10, Decimal("120"), sign=1)
    assert realized == Decimal("0")
    assert remaining == 10


def test_matches_across_multiple_queue_entries_fifo_order() -> None:
    queue: list[tuple[int, Decimal]] = [(5, Decimal("100")), (5, Decimal("110"))]
    realized, remaining = match_against(queue, 8, Decimal("120"), sign=1)
    # 5 @ 100 fully matched (5 * 20 = 100), then 3 of the 5 @ 110 (3 * 10 = 30)
    assert realized == Decimal("130")
    assert remaining == 0
    assert queue == [(2, Decimal("110"))]


def test_negative_sign_for_incoming_buy_against_short_queue() -> None:
    queue: list[tuple[int, Decimal]] = [(10, Decimal("120"))]
    realized, remaining = match_against(queue, 10, Decimal("100"), sign=-1)
    # short @ 120, covered @ 100 -> profit = 10 * (120 - 100) = 200
    assert realized == Decimal("200")
    assert remaining == 0


def test_exact_decimal_arithmetic_no_float_drift() -> None:
    """The whole point of #92: many fractional-price fills must sum exactly,
    not accumulate binary-float rounding error."""
    queue: list[tuple[int, Decimal]] = [(100, Decimal("100.1"))]
    realized, _ = match_against(queue, 100, Decimal("100.2"), sign=1)
    assert realized == Decimal("10.0")
    assert isinstance(realized, Decimal)
