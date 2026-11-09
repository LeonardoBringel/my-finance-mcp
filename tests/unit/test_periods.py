"""Period validation, days considered and period warnings."""

from datetime import date

import pytest

from my_finance_mcp.domain.errors import ToolInputError
from my_finance_mcp.domain.periods import (
    days_considered,
    period_warnings,
    validate_period,
    years_touched,
)

TODAY = date(2026, 10, 8)


def test_start_after_end_is_an_invalid_period():
    with pytest.raises(ToolInputError) as exc_info:
        validate_period(date(2026, 10, 2), date(2026, 10, 1))

    assert exc_info.value.code == "INVALID_PERIOD"
    assert exc_info.value.text == (
        "INVALID_PERIOD: start_date (2026-10-02) é posterior a "
        "end_date (2026-10-01)."
    )


def test_invalid_period_names_the_prefixed_arguments():
    with pytest.raises(ToolInputError) as exc_info:
        validate_period(date(2026, 10, 2), date(2026, 10, 1), prefix="base_")

    assert exc_info.value.text == (
        "INVALID_PERIOD: base_start_date (2026-10-02) é posterior a "
        "base_end_date (2026-10-01)."
    )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        # Exactly 36 calendar months.
        (date(2024, 1, 1), date(2026, 12, 31)),
        (date(2026, 10, 8), date(2026, 10, 8)),
    ],
)
def test_period_up_to_36_months_is_valid(start, end):
    assert validate_period(start, end) is None


def test_period_touching_37_months_is_too_long():
    with pytest.raises(ToolInputError) as exc_info:
        validate_period(date(2024, 1, 15), date(2027, 1, 1))

    assert exc_info.value.code == "PERIOD_TOO_LONG"
    assert exc_info.value.text == (
        "PERIOD_TOO_LONG: O período de start_date a end_date toca 37 meses; "
        "o máximo é 36. Divida a consulta em períodos de até 36 meses."
    )


def test_period_too_long_names_the_prefixed_arguments():
    with pytest.raises(ToolInputError) as exc_info:
        validate_period(date(2024, 1, 15), date(2027, 1, 1), prefix="target_")

    assert exc_info.value.message == (
        "O período de target_start_date a target_end_date toca 37 meses; "
        "o máximo é 36."
    )


@pytest.mark.parametrize(
    ("start", "end", "days", "warnings"),
    [
        # Current month: counts up to today only.
        (
            date(2026, 10, 1),
            date(2026, 10, 31),
            8,
            ("PARTIAL_PERIOD", "INCLUDES_FUTURE"),
        ),
        # Closed month in the past.
        (date(2026, 9, 1), date(2026, 9, 30), 30, ()),
        # Entirely in the future: the whole period counts.
        (date(2026, 11, 1), date(2026, 11, 30), 30, ("INCLUDES_FUTURE",)),
    ],
)
def test_days_considered_and_warnings_around_today(start, end, days, warnings):
    assert days_considered(start, end, TODAY) == days
    assert period_warnings(start, end, TODAY) == warnings


def test_single_day_equal_to_today_counts_one_day():
    assert days_considered(TODAY, TODAY, TODAY) == 1


def test_period_starting_today_and_ending_later_counts_only_today():
    assert days_considered(TODAY, date(2026, 10, 31), TODAY) == 1


def test_leap_day_to_first_of_march_counts_two_days():
    assert days_considered(date(2024, 2, 29), date(2024, 3, 1), TODAY) == 2


def test_period_touching_the_current_month_ending_today_is_partial_only():
    assert period_warnings(date(2026, 9, 15), TODAY, TODAY) == (
        "PARTIAL_PERIOD",
    )


def test_years_touched_includes_every_year_in_between():
    assert years_touched(date(2024, 12, 31), date(2026, 1, 1)) == [
        2024,
        2025,
        2026,
    ]
