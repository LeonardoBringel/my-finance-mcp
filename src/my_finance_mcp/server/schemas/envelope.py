"""Pydantic models of the tool result envelope."""

from datetime import date
from typing import Generic, Literal, TypeVar

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
