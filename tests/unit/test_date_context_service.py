"""Relative periods and weekday names computed from today."""

from datetime import date, timedelta

import pytest

from my_finance_mcp.services.date_context import (
    WEEKDAYS,
    RelativeRange,
    relative_periods,
)


def test_periods_on_a_thursday_in_the_middle_of_the_month():
    periods = relative_periods(date(2026, 10, 8))

    assert list(periods) == [
        "this_month",
        "last_month",
        "this_week",
        "this_year",
        "last_year",
        "last_3_closed_months",
    ]
    assert periods == {
        "this_month": RelativeRange(date(2026, 10, 1), date(2026, 10, 8), True),
        "last_month": RelativeRange(date(2026, 9, 1), date(2026, 9, 30), False),
        "this_week": RelativeRange(date(2026, 10, 5), date(2026, 10, 8), True),
        "this_year": RelativeRange(date(2026, 1, 1), date(2026, 10, 8), True),
        "last_year": RelativeRange(date(2025, 1, 1), date(2025, 12, 31), False),
        "last_3_closed_months": RelativeRange(
            date(2026, 7, 1), date(2026, 9, 30), False
        ),
    }


@pytest.mark.parametrize(
    ("today", "week_start"),
    [
        # Monday: the week is just today.
        (date(2026, 10, 5), date(2026, 10, 5)),
        # Sunday closes the week that started on Monday.
        (date(2026, 10, 11), date(2026, 10, 5)),
    ],
)
def test_week_starts_on_monday(today, week_start):
    this_week = relative_periods(today)["this_week"]

    assert (this_week.start, this_week.end) == (week_start, today)


def test_periods_on_new_year_day_roll_back_into_the_previous_year():
    periods = relative_periods(date(2026, 1, 1))

    def bounds(key):
        return (periods[key].start, periods[key].end)

    assert bounds("this_month") == (date(2026, 1, 1), date(2026, 1, 1))
    assert bounds("this_year") == (date(2026, 1, 1), date(2026, 1, 1))
    assert bounds("last_month") == (date(2025, 12, 1), date(2025, 12, 31))
    assert bounds("last_year") == (date(2025, 1, 1), date(2025, 12, 31))
    assert bounds("last_3_closed_months") == (
        date(2025, 10, 1),
        date(2025, 12, 31),
    )


def test_last_month_ends_on_leap_day():
    last_month = relative_periods(date(2028, 3, 15))["last_month"]

    assert (last_month.start, last_month.end) == (
        date(2028, 2, 1),
        date(2028, 2, 29),
    )


def test_current_periods_stay_partial_on_the_last_day_of_the_month():
    periods = relative_periods(date(2026, 10, 31))

    assert {key: period.partial for key, period in periods.items()} == {
        "this_month": True,
        "last_month": False,
        "this_week": True,
        "this_year": True,
        "last_year": False,
        "last_3_closed_months": False,
    }


def test_weekday_names_follow_date_weekday_from_monday_to_sunday():
    week = [date(2026, 10, 5) + timedelta(days=n) for n in range(7)]

    assert [WEEKDAYS[day.weekday()] for day in week] == [
        "segunda-feira",
        "terça-feira",
        "quarta-feira",
        "quinta-feira",
        "sexta-feira",
        "sábado",
        "domingo",
    ]
