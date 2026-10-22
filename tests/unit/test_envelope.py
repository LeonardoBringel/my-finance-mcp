"""Tool result envelope models."""

from datetime import date

import pytest
from pydantic import BaseModel, ValidationError

from my_finance_mcp.server.schemas.envelope import Meta, Period, ToolResult

REFERENCE_DATE = date(2026, 10, 8)
WARNING_CODES = [
    "NO_DATA",
    "PARTIAL_PERIOD",
    "INCLUDES_FUTURE",
    "SKIPPED_INVALID_ROWS",
    "INSUFFICIENT_HISTORY",
    "OVERLAPPING_PERIODS",
]


class SampleData(BaseModel):
    total: int


def test_meta_defaults():
    meta = Meta(reference_date=REFERENCE_DATE)

    assert meta.currency == "BRL"
    assert meta.period is None
    assert meta.truncated is False
    assert meta.warnings == []
    assert meta.reference_date == REFERENCE_DATE


def test_meta_default_warnings_are_not_shared_between_instances():
    first = Meta(reference_date=REFERENCE_DATE)
    first.warnings.append("NO_DATA")

    assert Meta(reference_date=REFERENCE_DATE).warnings == []


@pytest.mark.parametrize("code", WARNING_CODES)
def test_meta_accepts_each_warning_code(code):
    meta = Meta(reference_date=REFERENCE_DATE, warnings=[code])

    assert meta.warnings == [code]


def test_meta_rejects_unknown_warning_code():
    with pytest.raises(ValidationError):
        Meta(reference_date=REFERENCE_DATE, warnings=["FOO"])


def test_period_serializes_dates_as_iso_strings():
    period = Period(start_date=date(2026, 9, 1), end_date=date(2026, 9, 30))

    assert period.model_dump(mode="json") == {
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
    }


def test_meta_serializes_period_and_reference_date_as_iso_strings():
    meta = Meta(
        reference_date=REFERENCE_DATE,
        period=Period(start_date=date(2026, 9, 1), end_date=date(2026, 9, 30)),
    )

    assert meta.model_dump(mode="json") == {
        "currency": "BRL",
        "period": {"start_date": "2026-09-01", "end_date": "2026-09-30"},
        "reference_date": "2026-10-08",
        "truncated": False,
        "warnings": [],
    }


def test_tool_result_json_schema_requires_meta_and_data():
    schema = ToolResult[SampleData].model_json_schema()

    assert "meta" in schema["required"]
    assert "data" in schema["required"]


def test_meta_json_schema_does_not_require_period():
    schema = ToolResult[SampleData].model_json_schema()

    assert "period" not in schema["$defs"]["Meta"]["required"]
