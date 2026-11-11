"""Output of the get_date_context tool."""

from datetime import date
from typing import Literal

from pydantic import BaseModel


class RelativePeriod(BaseModel):
    start_date: date
    end_date: date
    partial: bool


class RelativePeriods(BaseModel):
    this_month: RelativePeriod
    last_month: RelativePeriod
    this_week: RelativePeriod
    this_year: RelativePeriod
    last_year: RelativePeriod
    last_3_closed_months: RelativePeriod


class DateContextData(BaseModel):
    today: date
    weekday: str
    timezone: Literal["America/Sao_Paulo"]
    periods: RelativePeriods
    available_years: list[int]
