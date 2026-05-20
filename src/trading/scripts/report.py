"""
Unified reporting script for day, week, and month reviews.

Usage
-----
    uv run report --period day              # today
    uv run report --period day 2025-04-24   # specific date (YYYY-MM-DD)
    uv run report --period week             # current week (Mon–Sun)
    uv run report --period week 2025-04-21  # week containing this date
    uv run report --period month            # current month
    uv run report --period month 2025-04    # specific month (YYYY-MM)
    uv run report --period month 2025-04-15 # month containing this date
"""

from __future__ import annotations

import argparse
import anyio
import sys
from datetime import UTC, date, datetime, timedelta

from trading.reports.engine import run_report


def _day_window(for_date: date) -> tuple[datetime, datetime]:
    start = datetime(for_date.year, for_date.month, for_date.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _week_window(for_date: date) -> tuple[datetime, datetime]:
    monday = for_date - timedelta(days=for_date.weekday())
    sunday = monday + timedelta(days=7)
    return (
        datetime(monday.year, monday.month, monday.day, tzinfo=UTC),
        datetime(sunday.year, sunday.month, sunday.day, tzinfo=UTC),
    )


def _month_window(for_date: date) -> tuple[datetime, datetime]:
    first = date(for_date.year, for_date.month, 1)
    if for_date.month == 12:
        next_first = date(for_date.year + 1, 1, 1)
    else:
        next_first = date(for_date.year, for_date.month + 1, 1)
    return (
        datetime(first.year, first.month, first.day, tzinfo=UTC),
        datetime(next_first.year, next_first.month, next_first.day, tzinfo=UTC),
    )


def _parse_date(arg: str, period: str) -> date:
    if period == "month" and len(arg) == 7:
        try:
            return date.fromisoformat(arg + "-01")
        except ValueError:
            pass
    try:
        return date.fromisoformat(arg)
    except ValueError:
        sys.exit(f"ERROR: Invalid date {arg!r}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a trading report")
    parser.add_argument(
        "--period",
        choices=["day", "week", "month"],
        required=True,
        help="Report period",
    )
    parser.add_argument(
        "date",
        nargs="?",
        default=None,
        help="Reference date (default: today). YYYY-MM-DD or YYYY-MM for month.",
    )
    args = parser.parse_args()

    for_date = _parse_date(args.date, args.period) if args.date else date.today()

    if args.period == "day":
        start, end = _day_window(for_date)
        title = f"DAY-END REVIEW — {for_date.strftime('%A, %d %B %Y')}"
    elif args.period == "week":
        start, end = _week_window(for_date)
        week_num = start.isocalendar()[1]
        end_str = (end - timedelta(days=1)).strftime("%d %b %Y")
        title = f"WEEK REVIEW — Week {week_num}, {start.strftime('%d %b')}–{end_str}"
    else:  # month
        start, end = _month_window(for_date)
        title = f"MONTH REVIEW — {start.strftime('%B %Y')}"

    anyio.run(run_report, start, end, title)


if __name__ == "__main__":
    main()
