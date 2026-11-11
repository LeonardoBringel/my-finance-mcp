"""Rounding of money and percentages, applied only when building output."""

from decimal import ROUND_HALF_UP, Decimal

_CENTS = Decimal("0.01")
_ONE_PLACE = Decimal("0.1")


def to_output(value: Decimal) -> float:
    """Round to cents, half up, as the my-finance dashboard shows them."""
    rounded = float(value.quantize(_CENTS, rounding=ROUND_HALF_UP))
    # Adding zero turns -0.0 into 0.0, so a tiny refund never prints "-0.0".
    return rounded + 0.0


def pct(numerator: Decimal, denominator: Decimal) -> float | None:
    """Percentage with one decimal place, half up; None without a base."""
    if denominator == 0:
        return None
    share = numerator / denominator * 100
    rounded = float(share.quantize(_ONE_PLACE, rounding=ROUND_HALF_UP))
    # Same as in to_output: a tiny negative share must not print "-0.0".
    return rounded + 0.0
