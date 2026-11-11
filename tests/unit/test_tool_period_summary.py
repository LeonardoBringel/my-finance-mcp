"""get_period_summary through an in-memory MCP client."""

from datetime import date
from decimal import Decimal

import anyio
import jsonschema
import pytest
from mcp import Client

from my_finance_mcp.auth.identity import AuthenticatedUser
from my_finance_mcp.data.rows import LoadedRows, TransactionRow
from my_finance_mcp.server.app import build_mcp_server
from my_finance_mcp.server.runtime import ToolRuntime
from my_finance_mcp.server.tools.summary import SummaryTools

TOOL_NAME = "get_period_summary"
TODAY = date(2026, 10, 8)
SEPTEMBER = {"start_date": "2026-09-01", "end_date": "2026-09-30"}
OCTOBER = {"start_date": "2026-10-01", "end_date": "2026-10-31"}

PUBLISHED_DESCRIPTION = (
    "Resume entradas, saídas, investimentos e saldo de um período. Use para "
    'perguntas gerais sobre um período ("como foi meu mês?", "quanto gastei '
    'no ano?"). Investimentos NÃO entram em saídas nem no saldo: são um total '
    "à parte. Se o período inclui o mês corrente, ele está incompleto "
    "(warning PARTIAL_PERIOD) e a média diária é a medida justa para comparar."
)


def tx(
    value: str,
    flow: str | None,
    category_type: str | None = "saida",
    installment: tuple[int, int] | None = None,
) -> TransactionRow:
    number, total = installment if installment else (None, None)
    return TransactionRow(
        id=1,
        year=2026,
        date=date(2026, 9, 10),
        value=Decimal(value),
        description=None,
        category_id=1,
        category_name="Categoria",
        category_type=category_type,
        flow=flow,
        installment_group="g1" if installment else None,
        installment_number=number,
        installment_total=total,
    )


SUMMARY_ROWS = (
    tx("5000.00", "entrada", "entrada"),
    tx("100.10", "saida", "saida"),
    tx("50.00", "saida", "ambos"),
    tx("20.00", "saida", None),
    tx("300.00", "investimento", "investimento"),
    tx("999.00", None, "xpto"),
)


class FakeUserData:
    """Serves fixed rows and records every period it is asked for."""

    def __init__(self, rows=(), skipped=0) -> None:
        self.loaded = LoadedRows(rows=tuple(rows), skipped=skipped)
        self.calls: list[tuple[date, date]] = []

    def transactions_between(self, start: date, end: date) -> LoadedRows:
        self.calls.append((start, end))
        return self.loaded

    def categories(self):
        raise AssertionError("the summary must not read categories")

    def category_counts(self):
        raise AssertionError("the summary must not read category counts")

    def available_years(self):
        raise AssertionError("the summary must not read available years")

    def installments(self):
        raise AssertionError("the summary must not read installments")


class FixedClock:
    def today(self) -> date:
        return TODAY


class Harness:
    def __init__(self, data: FakeUserData) -> None:
        self.data = data
        self.user_data_requests = 0
        runtime = ToolRuntime(
            FixedClock(),
            schema_check=lambda: None,
            user_data=self._user_data,
            identity=lambda: AuthenticatedUser(user_id=42, jti="j-1"),
        )
        self.server = build_mcp_server(runtime, tool_modules=(SummaryTools(),))

    def _user_data(self, user_id, cache):
        # The runtime asks for the user data right before it runs the
        # handler, so this count tells whether the handler ran.
        self.user_data_requests += 1
        return self.data

    def call(self, arguments):
        async def main():
            async with Client(self.server) as client:
                [tool] = (await client.list_tools()).tools
                result = await client.call_tool(TOOL_NAME, arguments)
                return tool, result

        return anyio.run(main)

    def call_ok(self, arguments):
        tool, result = self.call(arguments)
        assert result.is_error is False
        jsonschema.validate(result.structured_content, tool.output_schema)
        return result.structured_content

    def list_tool(self):
        async def main():
            async with Client(self.server) as client:
                [tool] = (await client.list_tools()).tools
                return tool

        return anyio.run(main)


def error_text(result) -> str:
    assert result.is_error is True
    [block] = result.content
    return block.text


def test_september_summary_matches_dashboard_totals():
    harness = Harness(FakeUserData(SUMMARY_ROWS))

    content = harness.call_ok(SEPTEMBER)

    assert content["data"] == {
        "entradas": 5000.0,
        "saidas": 170.1,
        "investimentos": 300.0,
        "saldo": 4829.9,
        "transaction_count": {"entrada": 1, "saida": 3, "investimento": 1},
        "avg_daily_expense": 5.67,
        "days_considered": 30,
        "inherited_installments_pct": 0.0,
    }
    assert content["meta"] == {
        "currency": "BRL",
        "period": SEPTEMBER,
        "reference_date": "2026-10-08",
        "truncated": False,
        "warnings": [],
    }
    assert harness.data.calls == [(date(2026, 9, 1), date(2026, 9, 30))]


def test_without_expenses_average_is_zero_and_inherited_share_is_null():
    rows = [tx("5000.00", "entrada", "entrada")]

    data = Harness(FakeUserData(rows)).call_ok(SEPTEMBER)["data"]

    assert data["saidas"] == 0.0
    assert data["avg_daily_expense"] == 0.0
    assert data["inherited_installments_pct"] is None


def test_inherited_share_counts_only_installments_after_the_first():
    rows = [
        tx("50.00", "saida", installment=(2, 3)),
        tx("30.00", "saida", installment=(1, 3)),
        tx("120.00", "saida"),
    ]

    data = Harness(FakeUserData(rows)).call_ok(SEPTEMBER)["data"]

    assert data["saidas"] == 200.0
    assert data["inherited_installments_pct"] == 25.0


@pytest.mark.parametrize(
    ("loaded", "warnings"),
    [
        (FakeUserData(SUMMARY_ROWS), ["PARTIAL_PERIOD", "INCLUDES_FUTURE"]),
        (
            FakeUserData(SUMMARY_ROWS, skipped=1),
            ["PARTIAL_PERIOD", "INCLUDES_FUTURE", "SKIPPED_INVALID_ROWS"],
        ),
        (
            FakeUserData(skipped=1),
            [
                "NO_DATA",
                "PARTIAL_PERIOD",
                "INCLUDES_FUTURE",
                "SKIPPED_INVALID_ROWS",
            ],
        ),
    ],
    ids=["current-month", "unreadable-row", "no-data"],
)
def test_current_month_warnings_follow_the_published_order(loaded, warnings):
    content = Harness(loaded).call_ok(OCTOBER)

    assert content["meta"]["warnings"] == warnings
    assert content["meta"]["period"] == OCTOBER
    assert content["data"]["days_considered"] == 8


def test_period_of_today_only_is_partial_with_one_day():
    today = {"start_date": "2026-10-08", "end_date": "2026-10-08"}

    content = Harness(FakeUserData(SUMMARY_ROWS)).call_ok(today)

    assert content["meta"]["warnings"] == ["PARTIAL_PERIOD"]
    assert content["meta"]["period"] == today
    assert content["data"]["days_considered"] == 1


def test_period_with_only_unknown_types_returns_zeros_and_no_data():
    rows = [tx("999.00", None, "xpto")]

    content = Harness(FakeUserData(rows)).call_ok(SEPTEMBER)

    assert content["data"] == {
        "entradas": 0.0,
        "saidas": 0.0,
        "investimentos": 0.0,
        "saldo": 0.0,
        "transaction_count": {"entrada": 0, "saida": 0, "investimento": 0},
        "avg_daily_expense": 0.0,
        "days_considered": 30,
        "inherited_installments_pct": None,
    }
    assert content["meta"]["warnings"] == ["NO_DATA"]


def test_reversed_period_is_refused_before_reading_data():
    harness = Harness(FakeUserData(SUMMARY_ROWS))

    _, result = harness.call(
        {"start_date": "2026-09-30", "end_date": "2026-09-01"}
    )

    assert error_text(result).startswith("INVALID_PERIOD:")
    assert harness.data.calls == []


def test_period_touching_more_than_36_months_is_refused_before_reading():
    harness = Harness(FakeUserData(SUMMARY_ROWS))

    _, result = harness.call(
        {"start_date": "2024-01-15", "end_date": "2027-01-01"}
    )

    assert error_text(result).startswith("PERIOD_TOO_LONG:")
    assert harness.data.calls == []


def test_period_of_exactly_36_months_is_read_once():
    harness = Harness(FakeUserData(SUMMARY_ROWS))
    period = {"start_date": "2024-01-01", "end_date": "2026-12-31"}

    content = harness.call_ok(period)

    assert content["meta"]["period"] == period
    assert harness.data.calls == [(date(2024, 1, 1), date(2026, 12, 31))]


@pytest.mark.parametrize("start_date", ["2026-02-30", "2026/09/01"])
def test_malformed_date_is_refused_by_the_sdk_before_the_tool_runs(
    start_date,
):
    harness = Harness(FakeUserData(SUMMARY_ROWS))

    _, result = harness.call(
        {"start_date": start_date, "end_date": "2026-09-30"}
    )

    text = error_text(result)
    assert not text.startswith(("INVALID_PERIOD:", "PERIOD_TOO_LONG:"))
    assert harness.user_data_requests == 0
    assert harness.data.calls == []


def test_input_schema_takes_two_required_dates_with_descriptions():
    tool = Harness(FakeUserData()).list_tool()

    schema = tool.input_schema
    properties = schema["properties"]
    assert sorted(schema["required"]) == ["end_date", "start_date"]
    assert properties.keys() == {"start_date", "end_date"}
    start, end = properties["start_date"], properties["end_date"]
    assert (start["type"], start["format"]) == ("string", "date")
    assert (end["type"], end["format"]) == ("string", "date")
    assert start["description"] == "Início do período, inclusivo."
    assert end["description"] == "Fim do período, inclusivo."


def test_description_is_the_published_text():
    tool = Harness(FakeUserData()).list_tool()

    assert tool.name == TOOL_NAME
    assert " ".join(tool.description.split()) == PUBLISHED_DESCRIPTION
