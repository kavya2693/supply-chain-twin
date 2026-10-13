"""Shared calendar facts: the history window, public holidays and the planning horizon.

Both the data generator and the twin read these, so a holiday the generator injects is the
same holiday the twin knows to plan for.
"""

from __future__ import annotations

import datetime as dt

HISTORY_START = dt.date(2024, 1, 1)
HISTORY_DAYS = 731  # two full years, 2024 is a leap year
HISTORY_END = HISTORY_START + dt.timedelta(days=HISTORY_DAYS - 1)

# Fixed-date public holidays plus a year-end peak. Moving religious holidays are given as
# explicit dates per year because they shift about eleven days each year.
_FIXED = [(1, 1), (12, 2), (12, 3)]
_MOVING = {
    2024: [dt.date(2024, 4, 10), dt.date(2024, 4, 11), dt.date(2024, 6, 16), dt.date(2024, 6, 17)],
    2025: [dt.date(2025, 3, 30), dt.date(2025, 3, 31), dt.date(2025, 6, 6), dt.date(2025, 6, 7)],
    2026: [dt.date(2026, 3, 20), dt.date(2026, 3, 21), dt.date(2026, 5, 27), dt.date(2026, 5, 28)],
}


def holidays(year: int) -> set[dt.date]:
    days = {dt.date(year, m, d) for m, d in _FIXED}
    days.update(_MOVING.get(year, []))
    return days


def is_holiday(day: dt.date) -> bool:
    return day in holidays(day.year)


def holiday_eve(day: dt.date) -> bool:
    """The two days before a holiday carry most of the stock-up buying."""
    return any(is_holiday(day + dt.timedelta(days=k)) for k in (1, 2))
