"""Pydantic models of the tool result envelope."""

from collections.abc import Iterable
from datetime import date
from typing import Generic, Literal, TypeVar, get_args

from pydantic import BaseModel

D = TypeVar("D")

WarningCode = Literal[
    "NO_DATA",
    "PARTIAL_PERIOD",
    "INCLUDES_FUTURE",
    "SKIPPED_INVALID_ROWS",
    "INSUFFICIENT_HISTORY",
    "OVERLAPPING_PERIODS",
]

_WARNING_ORDER = get_args(WarningCode)


def ordered_warnings(codes: Iterable[WarningCode]) -> tuple[WarningCode, ...]:
    """Warnings in the published order, each listed once."""
    present = set(codes)
    return tuple(code for code in _WARNING_ORDER if code in present)


class Period(BaseModel):
    start_date: date
    end_date: date


class Meta(BaseModel):
    currency: Literal["BRL"] = "BRL"
    period: Period | None = None
    reference_date: date
    truncated: bool = False
    warnings: list[WarningCode] = []


class ToolResult(BaseModel, Generic[D]):
    meta: Meta
    data: D
