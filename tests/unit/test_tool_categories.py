"""The list_categories tool through an in-memory MCP client."""

import json
from datetime import date

import anyio
import jsonschema
from mcp import Client

from my_finance_mcp.auth.identity import AuthenticatedUser
from my_finance_mcp.data.rows import CategoryCounts, CategoryRow
from my_finance_mcp.domain.category_types import flow_of
from my_finance_mcp.server.app import build_mcp_server
from my_finance_mcp.server.runtime import ToolRuntime
from my_finance_mcp.server.tools.categories import CategoryTools

DESCRIPTION = (
    "Lista as categorias do usuário com o tipo e a quantidade de transações. "
    "Use para descobrir os nomes exatos das categorias antes de filtrar "
    "outras tools, ou quando o usuário perguntar quais categorias tem. Não é "
    "preciso chamar antes de toda análise: as outras tools aceitam o nome "
    "sem diferenciar maiúsculas nem acentos e sugerem nomes parecidos quando "
    "não encontram."
)
TYPE_DESCRIPTION = (
    "Filtra pelo tipo bruto da categoria. Omitido, retorna todas."
)


def category(id: int, name: str, type: str | None) -> CategoryRow:
    return CategoryRow(id=id, name=name, type=type, flow=flow_of(type))


EXAMPLE_CATEGORIES = [
    category(1, "Salário", "entrada"),
    category(2, "Tesouro", "investimento"),
    category(3, "Mercado", "saida"),
]
EXAMPLE_COUNTS = CategoryCounts(
    by_category={1: 21, 2: 9, 3: 214}, uncategorized=3
)
EXAMPLE_DATA = {
    "categories": [
        {
            "name": "Mercado",
            "type": "saida",
            "flow": "saida",
            "transaction_count": 214,
        },
        {
            "name": "Salário",
            "type": "entrada",
            "flow": "entrada",
            "transaction_count": 21,
        },
        {
            "name": "Tesouro",
            "type": "investimento",
            "flow": "investimento",
            "transaction_count": 9,
        },
    ],
    "total_count": 3,
    "uncategorized_transaction_count": 3,
}


class FixedClock:
    def today(self) -> date:
        return date(2026, 10, 8)


def fixed_identity() -> AuthenticatedUser:
    return AuthenticatedUser(user_id=42, jti="j-1")


class CategoriesOnlyData:
    """User data that serves categories and fails on any other read."""

    def __init__(self, categories, counts) -> None:
        self._categories = categories
        self._counts = counts
        self.calls: list[str] = []

    def categories(self):
        self.calls.append("categories")
        return list(self._categories)

    def category_counts(self):
        self.calls.append("category_counts")
        return self._counts

    def transactions_between(self, start, end):
        self.calls.append("transactions_between")
        raise AssertionError("list_categories must not read transactions")

    def available_years(self):
        self.calls.append("available_years")
        raise AssertionError("list_categories must not read the years")

    def installments(self):
        self.calls.append("installments")
        raise AssertionError("list_categories must not read installments")


def categories_server(data: CategoriesOnlyData):
    runtime = ToolRuntime(
        FixedClock(),
        schema_check=lambda: None,
        user_data=lambda user_id, cache: data,
        identity=fixed_identity,
    )
    return build_mcp_server(runtime, tool_modules=(CategoryTools(),))


def run(server, action):
    async def main():
        async with Client(server) as client:
            [tool] = (await client.list_tools()).tools
            return tool, await action(client)

    return anyio.run(main)


def call(data: CategoriesOnlyData, arguments=None):
    """The published tool and the result of one call with these arguments."""

    async def action(client):
        return await client.call_tool("list_categories", arguments or {})

    return run(categories_server(data), action)


def example_data() -> CategoriesOnlyData:
    return CategoriesOnlyData(EXAMPLE_CATEGORIES, EXAMPLE_COUNTS)


def test_unfiltered_call_returns_the_published_example():
    data = example_data()

    tool, result = call(data)

    assert result.is_error is False
    jsonschema.validate(result.structured_content, tool.output_schema)
    assert result.structured_content["data"] == EXAMPLE_DATA
    [block] = result.content
    assert json.loads(block.text) == result.structured_content


def test_call_reads_categories_and_counts_but_never_transactions():
    data = example_data()

    call(data)

    assert sorted(data.calls) == ["categories", "category_counts"]


def test_meta_has_no_period_and_carries_the_envelope_defaults():
    _, result = call(example_data())

    assert result.structured_content["meta"] == {
        "currency": "BRL",
        "reference_date": "2026-10-08",
        "truncated": False,
        "warnings": [],
    }


def test_filter_without_expenses_leaves_out_the_uncategorized_count():
    tool, result = call(example_data(), {"type": "entrada"})

    jsonschema.validate(result.structured_content, tool.output_schema)
    assert result.structured_content["data"] == {
        "categories": [
            {
                "name": "Salário",
                "type": "entrada",
                "flow": "entrada",
                "transaction_count": 21,
            }
        ],
        "total_count": 1,
    }


def test_expense_filter_keeps_the_uncategorized_count():
    _, result = call(example_data(), {"type": "saida"})

    data = result.structured_content["data"]
    assert data["uncategorized_transaction_count"] == 3
    assert [c["name"] for c in data["categories"]] == ["Mercado"]


def test_more_than_200_categories_mark_the_result_truncated():
    rows = [category(i, f"Cat {i:03d}", "saida") for i in range(201)]
    data = CategoriesOnlyData(rows, CategoryCounts({0: 1}, uncategorized=0))

    tool, result = call(data)

    jsonschema.validate(result.structured_content, tool.output_schema)
    assert result.structured_content["meta"]["truncated"] is True
    assert len(result.structured_content["data"]["categories"]) == 200
    assert result.structured_content["data"]["total_count"] == 201
    assert result.structured_content["meta"]["warnings"] == []


def test_no_data_ignores_transactions_of_categories_cut_off_at_200():
    rows = [category(i, f"Cat {i:03d}", "saida") for i in range(201)]
    data = CategoriesOnlyData(rows, CategoryCounts({200: 1}, uncategorized=0))

    _, result = call(data)

    assert result.structured_content["meta"]["truncated"] is True
    assert len(result.structured_content["data"]["categories"]) == 200
    assert result.structured_content["meta"]["warnings"] == ["NO_DATA"]


def test_income_filter_with_only_uncategorized_transactions_has_no_data():
    data = CategoriesOnlyData([], CategoryCounts({}, uncategorized=3))

    _, result = call(data, {"type": "entrada"})

    assert result.structured_content["data"] == {
        "categories": [],
        "total_count": 0,
    }
    assert result.structured_content["meta"]["warnings"] == ["NO_DATA"]


def test_categories_without_transactions_have_no_data():
    counts = CategoryCounts({}, uncategorized=0)
    data = CategoriesOnlyData(EXAMPLE_CATEGORIES, counts)

    _, result = call(data)

    assert result.structured_content["meta"]["warnings"] == ["NO_DATA"]


def test_uncategorized_transactions_alone_count_as_data_without_filter():
    data = CategoriesOnlyData([], CategoryCounts({}, uncategorized=3))

    _, result = call(data)

    assert result.structured_content["meta"]["warnings"] == []


def test_unknown_type_argument_is_refused_before_the_handler_runs():
    data = example_data()

    _, result = call(data, {"type": "outro"})

    assert result.is_error is True
    assert result.structured_content is None
    assert data.calls == []


def test_published_input_schema_and_description():
    tool, _ = call(example_data())

    assert " ".join(tool.description.split()) == DESCRIPTION
    assert list(tool.input_schema["properties"]) == ["type"]
    assert tool.input_schema.get("required", []) == []
    prop = tool.input_schema["properties"]["type"]
    assert prop["default"] is None
    assert prop["description"] == TYPE_DESCRIPTION
    enum_branch = next(b for b in prop["anyOf"] if "enum" in b)
    assert sorted(enum_branch["enum"]) == [
        "ambos",
        "entrada",
        "investimento",
        "saida",
    ]
    assert {"type": "null"} in prop["anyOf"]
    assert len(prop["anyOf"]) == 2
