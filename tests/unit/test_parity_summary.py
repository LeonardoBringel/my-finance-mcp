"""get_period_summary matches the my-finance dashboard, month by month."""

import calendar
import sys

import psycopg
import pytest

from my_finance_mcp.data.repositories import transactions_repository
from my_finance_mcp.data.repositories.transactions_repository import (
    SELECT_BY_YEARS,
)
from tests.unit.parity_support import (
    FERNET,
    YearFilteringEngine,
    call_tool,
    cents,
    load_dataset,
    load_golden,
    parity_server,
)

GOLDEN = load_golden()
DATASET = load_dataset()
COMPARED_FIELDS = (
    "entradas",
    "saidas",
    "investimentos",
    "saldo",
    "avg_daily_expense",
)
# Top-level packages of the my-finance code base.
MY_FINANCE_PACKAGES = ("repositories", "models", "utils")


def month_period(month: str) -> dict[str, str]:
    year, number = map(int, month.split("-"))
    last_day = calendar.monthrange(year, number)[1]
    return {
        "start_date": f"{month}-01",
        "end_date": f"{month}-{last_day:02d}",
    }


def summary_of(month: str, server=None) -> dict:
    return call_tool(
        server or parity_server(), "get_period_summary", month_period(month)
    )


@pytest.mark.parametrize("month", sorted(GOLDEN["months"]))
def test_month_summary_matches_the_dashboard_to_the_cent(month):
    expected = GOLDEN["months"][month]["summary"]

    data = summary_of(month)["data"]

    assert {field: cents(data[field]) for field in COMPARED_FIELDS} == {
        field: cents(expected[field]) for field in COMPARED_FIELDS
    }


def test_engine_serves_only_the_transactions_of_the_requested_years():
    engine = YearFilteringEngine(DATASET)

    loaded = transactions_repository.list_by_years(
        engine, FERNET, DATASET.user_id, [2025]
    )

    expected_ids = {
        row["id"] for row in DATASET.transactions if row["year"] == 2025
    }
    assert {row.id for row in loaded[2025].rows} == expected_ids
    assert loaded[2025].skipped == 0


def test_unreadable_date_is_reported_in_the_months_of_its_year():
    [row] = [r for r in DATASET.transactions if r["date"] == "2026-02-30"]
    assert row["year"] == 2026
    server = parity_server()

    february_2026 = summary_of("2026-02", server)["meta"]["warnings"]
    february_2025 = summary_of("2025-02", server)["meta"]["warnings"]

    assert "SKIPPED_INVALID_ROWS" in february_2026
    assert "SKIPPED_INVALID_ROWS" not in february_2025


def test_parity_runs_without_my_finance_code_or_a_database(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the parity test must not open a connection")

    monkeypatch.setattr(psycopg.Connection, "connect", refuse)
    monkeypatch.setattr(psycopg, "connect", refuse)
    engine = YearFilteringEngine(DATASET)

    summary_of("2026-10", parity_server(engine))

    assert engine.queries() == [
        (SELECT_BY_YEARS, {"user_id": DATASET.user_id, "years": [2026]})
    ]
    loaded = [
        name
        for name in sys.modules
        if name.split(".")[0] in MY_FINANCE_PACKAGES
    ]
    assert loaded == []
