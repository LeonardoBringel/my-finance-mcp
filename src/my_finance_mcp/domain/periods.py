"""Period rules shared by every tool that receives a date range."""

from datetime import date

from my_finance_mcp.domain.errors import ToolInputError
from my_finance_mcp.server.schemas.envelope import WarningCode

MAX_PERIOD_MONTHS = 36


def months_touched(start: date, end: date) -> int:
    """Distinct calendar months the period touches, both ends included."""
    return (end.year - start.year) * 12 + end.month - start.month + 1


def years_touched(start: date, end: date) -> list[int]:
    return list(range(start.year, end.year + 1))


def validate_period(start: date, end: date, prefix: str = "") -> None:
    """Reject reversed or too long periods.

    The prefix names the arguments of tools that take more than one period,
    such as base_start_date and target_start_date.
    """
    p = prefix
    if start > end:
        raise ToolInputError(
            "INVALID_PERIOD",
            f"{p}start_date ({start}) é posterior a {p}end_date ({end}).",
        )
    months = months_touched(start, end)
    if months > MAX_PERIOD_MONTHS:
        raise ToolInputError(
            "PERIOD_TOO_LONG",
            f"O período de {p}start_date a {p}end_date toca {months} meses; "
            f"o máximo é {MAX_PERIOD_MONTHS}.",
            f"Divida a consulta em períodos de até {MAX_PERIOD_MONTHS} meses.",
        )


def days_considered(start: date, end: date, today: date) -> int:
    """Days that already happened, or the whole period if it is all future."""
    last = end if start > today else min(end, today)
    return (last - start).days + 1


def period_warnings(
    start: date, end: date, today: date
) -> tuple[WarningCode, ...]:
    warnings: list[WarningCode] = []
    current_month = (today.year, today.month)
    if (start.year, start.month) <= current_month <= (end.year, end.month):
        warnings.append("PARTIAL_PERIOD")
    if end > today:
        warnings.append("INCLUDES_FUTURE")
    return tuple(warnings)
