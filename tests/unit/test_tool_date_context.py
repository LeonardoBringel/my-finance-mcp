"""The get_date_context tool through an in-memory MCP client."""

from datetime import date

import anyio
import jsonschema
from mcp import Client

from my_finance_mcp.auth.identity import AuthenticatedUser
from my_finance_mcp.server.app import build_mcp_server
from my_finance_mcp.server.runtime import ToolRuntime
from my_finance_mcp.server.tools.calendar import CalendarTools

TOOL_NAME = "get_date_context"

PUBLISHED_DESCRIPTION = (
    "Devolve a data de hoje do usuário (fuso America/Sao_Paulo) e os limites "
    "dos períodos relativos usados pelas outras tools. Chame antes de "
    'interpretar expressões como "este mês", "mês passado", "esta semana", '
    '"últimos 3 meses" ou um nome de mês sem ano, se você não souber a data '
    "de hoje no fuso do usuário. Não recebe argumentos."
)

DATA_ON_2026_10_08 = {
    "today": "2026-10-08",
    "weekday": "quinta-feira",
    "timezone": "America/Sao_Paulo",
    "periods": {
        "this_month": {
            "start_date": "2026-10-01",
            "end_date": "2026-10-08",
            "partial": True,
        },
        "last_month": {
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
            "partial": False,
        },
        "this_week": {
            "start_date": "2026-10-05",
            "end_date": "2026-10-08",
            "partial": True,
        },
        "this_year": {
            "start_date": "2026-01-01",
            "end_date": "2026-10-08",
            "partial": True,
        },
        "last_year": {
            "start_date": "2025-01-01",
            "end_date": "2025-12-31",
            "partial": False,
        },
        "last_3_closed_months": {
            "start_date": "2026-07-01",
            "end_date": "2026-09-30",
            "partial": False,
        },
    },
    "available_years": [2024, 2025, 2026],
}


class FixedClock:
    def __init__(self, today: date) -> None:
        self._today = today

    def today(self) -> date:
        return self._today


class YearsOnlyData:
    """User data that only answers which years have transactions."""

    def __init__(self, years: list[int]) -> None:
        self._years = years

    def available_years(self) -> list[int]:
        return list(self._years)

    def categories(self):
        raise AssertionError("categories must not be read")

    def category_counts(self):
        raise AssertionError("category counts must not be read")

    def transactions_between(self, start, end):
        raise AssertionError("transactions must not be read")

    def installments(self):
        raise AssertionError("installments must not be read")


def fixed_identity() -> AuthenticatedUser:
    return AuthenticatedUser(user_id=42, jti="j-1")


def calendar_server(years, today=date(2026, 10, 8)):
    data = YearsOnlyData(years)
    runtime = ToolRuntime(
        FixedClock(today),
        schema_check=lambda: None,
        user_data=lambda user_id, cache: data,
        identity=fixed_identity,
    )
    return build_mcp_server(runtime, tool_modules=(CalendarTools(),))


def list_and_call(server):
    async def main():
        async with Client(server) as client:
            [tool] = (await client.list_tools()).tools
            return tool, await client.call_tool(TOOL_NAME, {})

    return anyio.run(main)


def test_data_on_2026_10_08_is_the_published_example_and_fits_the_schema():
    tool, result = list_and_call(calendar_server([2024, 2025, 2026]))

    assert result.is_error is False
    assert result.structured_content["data"] == DATA_ON_2026_10_08
    assert result.structured_content["meta"] == {
        "currency": "BRL",
        "reference_date": "2026-10-08",
        "truncated": False,
        "warnings": [],
    }
    jsonschema.validate(result.structured_content, tool.output_schema)


def test_available_years_come_out_ascending():
    _, result = list_and_call(calendar_server([2026, 2024, 2025]))

    assert result.structured_content["data"]["available_years"] == [
        2024,
        2025,
        2026,
    ]


def test_user_without_transactions_gets_success_with_no_data_warning():
    tool, result = list_and_call(calendar_server([]))

    assert result.is_error is False
    jsonschema.validate(result.structured_content, tool.output_schema)
    assert result.structured_content["data"]["available_years"] == []
    assert result.structured_content["meta"]["warnings"] == ["NO_DATA"]


def test_meta_has_no_period_and_reference_date_is_today_in_data():
    _, result = list_and_call(calendar_server([2026], today=date(2027, 1, 1)))

    meta = result.structured_content["meta"]
    assert "period" not in meta
    assert meta["reference_date"] == "2027-01-01"
    assert result.structured_content["data"]["today"] == "2027-01-01"


def test_tool_takes_no_arguments_and_publishes_its_description():
    tool, _ = list_and_call(calendar_server([2026]))

    assert tool.name == TOOL_NAME
    assert tool.input_schema.get("properties", {}) == {}
    assert tool.input_schema.get("required", []) == []
    assert " ".join(tool.description.split()) == PUBLISHED_DESCRIPTION
