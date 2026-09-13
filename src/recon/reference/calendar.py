"""Banking-day calendar for the generation window.

ACH does not settle at weekends or on US federal holidays, so a deposit instructed on
a Friday posts on the following Monday and a payment cycle that lands on Thanksgiving
slips two days.  Group 2's arrival model and Group 4's bank generator both need to
know that; putting the calendar here means they agree about it.

Pure data and pure functions — no clock, no I/O.  The holiday list is stated
literally rather than computed from rules, because the observed dates (a Saturday
holiday observed on the preceding Friday, a Sunday one on the following Monday) are
exactly the cases a rule engine gets subtly wrong, and there are only twelve of them.

Coverage runs from ``2025-07-01`` to ``2026-07-31``: a month past the window end, so
that ``add_banking_days`` near the boundary does not silently fall off a cliff.
"""

from __future__ import annotations

from datetime import date, timedelta

__all__ = [
    "FEDERAL_HOLIDAYS",
    "CALENDAR_START",
    "CALENDAR_END",
    "is_weekend",
    "is_banking_day",
    "next_banking_day",
    "previous_banking_day",
    "add_banking_days",
    "banking_days_between",
]

CALENDAR_START = date(2025, 7, 1)
CALENDAR_END = date(2026, 7, 31)

#: US federal holidays, as observed, covering the window plus a month of slack.
FEDERAL_HOLIDAYS: frozenset[date] = frozenset(
    {
        date(2025, 7, 4),    # Independence Day
        date(2025, 9, 1),    # Labor Day
        date(2025, 10, 13),  # Columbus Day
        date(2025, 11, 11),  # Veterans Day
        date(2025, 11, 27),  # Thanksgiving
        date(2025, 12, 25),  # Christmas Day
        date(2026, 1, 1),    # New Year's Day
        date(2026, 1, 19),   # Birthday of Martin Luther King, Jr.
        date(2026, 2, 16),   # Washington's Birthday
        date(2026, 5, 25),   # Memorial Day
        date(2026, 6, 19),   # Juneteenth
        date(2026, 7, 3),    # Independence Day observed (4 July 2026 is a Saturday)
    }
)


def is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def is_banking_day(day: date) -> bool:
    return not is_weekend(day) and day not in FEDERAL_HOLIDAYS


def next_banking_day(day: date) -> date:
    """The first banking day strictly after ``day``."""
    candidate = day + timedelta(days=1)
    while not is_banking_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def previous_banking_day(day: date) -> date:
    """The last banking day strictly before ``day``."""
    candidate = day - timedelta(days=1)
    while not is_banking_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def add_banking_days(day: date, count: int) -> date:
    """Advance (or retreat) by ``count`` banking days.

    ``count == 0`` snaps a non-banking day forward to the next banking day, which is
    the behaviour a settlement date wants: "effective today" on a Saturday means
    Monday.
    """
    if count == 0:
        return day if is_banking_day(day) else next_banking_day(day)
    step = next_banking_day if count > 0 else previous_banking_day
    current = day
    for _ in range(abs(count)):
        current = step(current)
    return current


def banking_days_between(start: date, end: date) -> int:
    """Count banking days in ``(start, end]``.  Negative if ``end`` precedes ``start``."""
    if end == start:
        return 0
    sign = 1 if end > start else -1
    low, high = (start, end) if sign == 1 else (end, start)
    count = 0
    current = low + timedelta(days=1)
    while current <= high:
        if is_banking_day(current):
            count += 1
        current += timedelta(days=1)
    return sign * count
