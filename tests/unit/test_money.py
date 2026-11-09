"""Output rounding of money and percentages."""

import math
from decimal import Decimal

import pytest

from my_finance_mcp.domain.money import pct, to_output


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Half up, not banker's rounding: 2.345 would round to 2.34 otherwise.
        (Decimal("2.345"), 2.35),
        (Decimal("2.344"), 2.34),
    ],
)
def test_money_rounds_half_up_to_two_places_as_float(value, expected):
    result = to_output(value)

    assert result == expected
    assert type(result) is float


def test_money_rounding_to_negative_zero_returns_positive_zero():
    result = to_output(Decimal("-0.004"))

    assert result == 0.0
    assert math.copysign(1, result) == 1


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        (Decimal(1), Decimal(8), 12.5),
        (Decimal(1), Decimal(3), 33.3),
        # 0.05 % rounds half up to one place.
        (Decimal("0.0005"), Decimal(1), 0.1),
    ],
)
def test_percentage_rounds_half_up_to_one_place(
    numerator, denominator, expected
):
    result = pct(numerator, denominator)

    assert result == expected
    assert type(result) is float


def test_percentage_with_zero_denominator_is_none():
    assert pct(Decimal(5), Decimal(0)) is None
