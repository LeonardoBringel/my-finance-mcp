"""Relative periods the tools use to interpret "this month", "last year"."""

from dataclasses import dataclass
from datetime import date, timedelta

# Indexed by date.weekday(), where Monday is 0.
WEEKDAYS: tuple[str, ...] = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)


@dataclass(frozen=True)
class RelativeRange:
    start: date
    end: date
    partial: bool  # True while the period still includes today


def _last_day_of_previous_month(day: date) -> date:
    return day.replace(day=1) - timedelta(days=1)


def relative_periods(today: date) -> dict[str, RelativeRange]:
    """Current periods end today; past periods are closed months or years."""
    month_start = today.replace(day=1)
    last_month_end = _last_day_of_previous_month(today)
    oldest_closed_month_end = _last_day_of_previous_month(
        _last_day_of_previous_month(last_month_end)
    )
    return {
        "this_month": RelativeRange(month_start, today, True),
        "last_month": RelativeRange(
            last_month_end.replace(day=1), last_month_end, False
        ),
        "this_week": RelativeRange(
            today - timedelta(days=today.weekday()), today, True
        ),
        "this_year": RelativeRange(today.replace(month=1, day=1), today, True),
        "last_year": RelativeRange(
            date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), False
        ),
        "last_3_closed_months": RelativeRange(
            oldest_closed_month_end.replace(day=1), last_month_end, False
        ),
    }
