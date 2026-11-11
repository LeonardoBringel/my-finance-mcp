"""Tool catalog: closed inputs, read-only tools and the shared call behavior."""

import json
import logging
from datetime import date
from decimal import Decimal

import anyio
import jsonschema
import pytest
from mcp import Client
from mcp_types import ToolAnnotations
from pydantic import BaseModel

from my_finance_mcp.auth.identity import AuthenticatedUser
from my_finance_mcp.data.rows import (
    CategoryCounts,
    CategoryRow,
    LoadedRows,
    TransactionRow,
)
from my_finance_mcp.server.app import TOOL_MODULES, build_mcp_server
from my_finance_mcp.server.runtime import ToolRuntime
from my_finance_mcp.server.runtime import logger as runtime_logger
from my_finance_mcp.server.schemas.envelope import ToolResult
from tests.unit.test_mcp_server import with_client


class FixedClock:
    def today(self) -> date:
        return date(2026, 10, 8)


def fixed_identity() -> AuthenticatedUser:
    return AuthenticatedUser(user_id=42, jti="j-1")


def make_runtime() -> ToolRuntime:
    # The catalog is only listed here, so no tool call reaches these.
    return ToolRuntime(
        FixedClock(),
        schema_check=lambda: None,
        user_data=lambda user_id, cache: None,
        identity=fixed_identity,
    )


class Output(BaseModel):
    total: float


class Filter(BaseModel):
    user_id: int
    label: str


class SafeFilter(BaseModel):
    label: str


def schema_properties(node):
    """Yield every property-name mapping found at any depth of a schema."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                yield value
                children = value.values()
            else:
                children = [value]
            for child in children:
                yield from schema_properties(child)
    elif isinstance(node, list):
        for child in node:
            yield from schema_properties(child)


def assert_closed_catalog(tools):
    for tool in tools:
        for properties in schema_properties(tool.input_schema):
            assert (
                "user_id" not in properties
            ), f"tool {tool.name} exposes user_id in its input schema"
        annotations = tool.annotations
        assert (
            annotations is not None
            and annotations.read_only_hint is True
            and annotations.open_world_hint is False
        ), f"tool {tool.name} is not declared read-only and closed-world"


def list_tools(server):
    async def main():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    return anyio.run(main)


def make_server(*module_classes):
    runtime = make_runtime()
    return build_mcp_server(
        runtime, tool_modules=tuple(cls() for cls in module_classes)
    )


class GoodModule:
    def register(self, finance_tool, runtime):
        @finance_tool
        def good_tool(filters: SafeFilter) -> ToolResult[Output]:
            """Tool with no user_id anywhere."""
            raise NotImplementedError

        @finance_tool
        def good_tool_without_arguments() -> ToolResult[Output]:
            """Tool with no input at all."""
            raise NotImplementedError


class DirectUserIdModule:
    def register(self, finance_tool, runtime):
        @finance_tool
        def tool_with_direct_user_id(user_id: int) -> ToolResult[Output]:
            """Tool taking user_id as a parameter."""
            raise NotImplementedError


class NestedUserIdModule:
    def register(self, finance_tool, runtime):
        @finance_tool
        def tool_with_model_user_id(filters: Filter) -> ToolResult[Output]:
            """Tool whose argument model has a user_id field."""
            raise NotImplementedError


class ListUserIdModule:
    def register(self, finance_tool, runtime):
        @finance_tool
        def tool_with_list_user_id(
            filters: list[Filter],
        ) -> ToolResult[Output]:
            """Tool whose argument is a list of models with user_id."""
            raise NotImplementedError


class OptionalUserIdModule:
    def register(self, finance_tool, runtime):
        @finance_tool
        def tool_with_optional_user_id(
            filters: Filter | None = None,
        ) -> ToolResult[Output]:
            """Tool whose argument is an optional model with user_id."""
            raise NotImplementedError


def test_production_catalog_is_closed_and_publishes_output_schemas():
    server = build_mcp_server(
        make_runtime(),
        tool_modules=TOOL_MODULES,
    )

    tools = list_tools(server)

    assert len(tools) == 3
    assert_closed_catalog(tools)
    for tool in tools:
        assert tool.output_schema is not None, tool.name
        assert {"meta", "data"} <= set(tool.output_schema["required"])


def test_correct_tools_pass_the_check():
    tools = list_tools(make_server(GoodModule))

    assert [tool.name for tool in tools] == [
        "good_tool",
        "good_tool_without_arguments",
    ]
    assert_closed_catalog(tools)


def test_direct_user_id_parameter_fails_citing_the_tool():
    tools = list_tools(make_server(DirectUserIdModule))

    assert "user_id" in tools[0].input_schema["properties"]
    with pytest.raises(AssertionError, match="tool_with_direct_user_id"):
        assert_closed_catalog(tools)


@pytest.mark.parametrize(
    ("module", "name"),
    [
        (NestedUserIdModule, "tool_with_model_user_id"),
        (ListUserIdModule, "tool_with_list_user_id"),
        (OptionalUserIdModule, "tool_with_optional_user_id"),
    ],
    ids=["model", "list-of-models", "optional-model"],
)
def test_user_id_inside_defs_fails_citing_the_tool(module, name):
    [tool] = list_tools(make_server(module))

    assert "user_id" not in tool.input_schema.get("properties", {})
    assert "user_id" in tool.input_schema["$defs"]["Filter"]["properties"]
    with pytest.raises(AssertionError, match=name):
        assert_closed_catalog([tool])


def test_failing_tool_is_cited_among_correct_ones():
    tools = list_tools(make_server(GoodModule, DirectUserIdModule))

    with pytest.raises(AssertionError, match="tool_with_direct_user_id"):
        assert_closed_catalog(tools)


@pytest.mark.parametrize(
    "annotations",
    [
        None,
        {"read_only_hint": True},
        {"read_only_hint": False, "open_world_hint": False},
        {"read_only_hint": True, "open_world_hint": True},
    ],
    ids=["none", "no-open-world", "not-read-only", "open-world"],
)
def test_tool_without_read_only_annotations_fails(annotations):
    server = make_server()
    kwargs = {}
    if annotations is not None:
        kwargs["annotations"] = ToolAnnotations(**annotations)

    @server.tool(structured_output=True, **kwargs)
    def unannotated_tool() -> ToolResult[Output]:
        """Tool registered without the read-only annotations."""
        raise NotImplementedError

    with pytest.raises(AssertionError, match="unannotated_tool"):
        assert_closed_catalog(list_tools(server))


USER_A = 42
USER_B = 99
SEPTEMBER = {"start_date": "2026-09-01", "end_date": "2026-09-30"}
B_MARKERS = ["ZZCATB", "987654.32"]


def tx(id: int, category: CategoryRow, value: str) -> TransactionRow:
    return TransactionRow(
        id=id,
        year=2026,
        date=date(2026, 9, 10),
        value=Decimal(value),
        description=None,
        category_id=category.id,
        category_name=category.name,
        category_type=category.type,
        flow=category.flow,
        installment_group=None,
        installment_number=None,
        installment_total=None,
    )


MERCADO = CategoryRow(id=1, name="Mercado", type="saida", flow="saida")
SALARIO = CategoryRow(id=2, name="Salário", type="entrada", flow="entrada")
ZZCATB = CategoryRow(id=3, name="ZZCATB", type="saida", flow="saida")

RECORDS = {
    USER_A: (
        [MERCADO, SALARIO],
        (tx(1, MERCADO, "120.50"), tx(2, SALARIO, "5000.00")),
    ),
    USER_B: ([ZZCATB], (tx(3, ZZCATB, "987654.32"),)),
}


class RecordsData:
    """One user's categories and transactions, served from RECORDS."""

    def __init__(self, user_id: int) -> None:
        self._categories, self._rows = RECORDS[user_id]

    def categories(self) -> list[CategoryRow]:
        return list(self._categories)

    def category_counts(self) -> CategoryCounts:
        counts: dict[int, int] = {}
        for row in self._rows:
            counts[row.category_id] = counts.get(row.category_id, 0) + 1
        return CategoryCounts(by_category=counts, uncategorized=0)

    def transactions_between(self, start: date, end: date) -> LoadedRows:
        rows = tuple(row for row in self._rows if start <= row.date <= end)
        return LoadedRows(rows=rows, skipped=0)

    def available_years(self) -> list[int]:
        return sorted({row.year for row in self._rows})

    def installments(self) -> LoadedRows:
        raise AssertionError("no base tool reads installments")


def production_server(user_id: int = USER_A):
    """Production catalog called as user_id, with data served per user."""
    runtime = ToolRuntime(
        FixedClock(),
        schema_check=lambda: None,
        user_data=lambda requested_id, cache: RecordsData(requested_id),
        identity=lambda: AuthenticatedUser(user_id=user_id, jti="j-1"),
    )
    return build_mcp_server(runtime)


BASE_CALLS = [
    ("get_date_context", {}),
    ("list_categories", {}),
    ("get_period_summary", SEPTEMBER),
]


def call_tools(server, calls):
    """Successful results of each call, checked against the outputSchema."""

    async def action(client):
        schemas = {
            tool.name: tool.output_schema
            for tool in (await client.list_tools()).tools
        }
        results = []
        for name, arguments in calls:
            result = await client.call_tool(name, arguments)
            assert result.is_error is False, name
            jsonschema.validate(result.structured_content, schemas[name])
            results.append(result)
        return results

    return with_client(server, action)


def test_identical_calls_return_identical_structured_content():
    calls = [call for call in BASE_CALLS for _ in range(2)]

    results = call_tools(production_server(), calls)

    contents = [result.structured_content for result in results]
    for first, second in zip(contents[::2], contents[1::2]):
        assert first == second


def test_calls_as_one_user_never_show_another_users_data():
    calls = [("list_categories", {}), ("get_period_summary", SEPTEMBER)]

    as_a = call_tools(production_server(USER_A), calls)
    as_b = call_tools(production_server(USER_B), calls)

    categories, summary = (result.structured_content for result in as_a)
    assert [c["name"] for c in categories["data"]["categories"]] == [
        "Mercado",
        "Salário",
    ]
    assert summary["data"]["saidas"] == 120.5
    for result in as_a:
        [block] = result.content
        serialized = json.dumps(result.structured_content, ensure_ascii=False)
        for marker in B_MARKERS:
            assert marker not in block.text
            assert marker not in serialized
    # The same calls as B do carry the markers, so their absence above is
    # the user boundary at work, not markers that never reach a response.
    for result in as_b:
        [block] = result.content
        assert any(marker in block.text for marker in B_MARKERS)


def test_each_call_logs_exactly_one_line_from_the_runtime(caplog):
    caplog.set_level(logging.DEBUG)

    call_tools(production_server(), BASE_CALLS)

    # Only the SDK and the event loop are left out: a line from a handler,
    # under any logger of ours or the root one, would show up here.
    ours = [
        record
        for record in caplog.records
        if record.name.split(".")[0] not in {"mcp", "asyncio"}
    ]
    assert [record.name for record in ours] == [runtime_logger.name] * 3
    assert [record.getMessage().split()[:2] for record in ours] == [
        ["tool=get_date_context", "status=ok"],
        ["tool=list_categories", "status=ok"],
        ["tool=get_period_summary", "status=ok"],
    ]


def test_every_tool_returns_brl_and_the_clock_today_in_meta():
    results = call_tools(production_server(), BASE_CALLS)

    for result in results:
        meta = result.structured_content["meta"]
        assert meta["currency"] == "BRL"
        assert meta["reference_date"] == "2026-10-08"
