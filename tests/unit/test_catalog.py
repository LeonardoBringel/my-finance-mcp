"""Closed tool catalog: no user_id input and read-only annotations."""

from datetime import date

import anyio
import pytest
from mcp import Client
from mcp_types import ToolAnnotations
from pydantic import BaseModel

from my_finance_mcp.auth.identity import AuthenticatedUser
from my_finance_mcp.server.app import TOOL_MODULES, build_mcp_server
from my_finance_mcp.server.runtime import ToolRuntime
from my_finance_mcp.server.schemas.envelope import ToolResult


class FixedClock:
    def today(self) -> date:
        return date(2026, 10, 8)


def fixed_identity() -> AuthenticatedUser:
    return AuthenticatedUser(user_id=42, jti="j-1")


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
    runtime = ToolRuntime(FixedClock(), identity=fixed_identity)
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


def test_production_catalog_is_closed():
    server = build_mcp_server(
        ToolRuntime(FixedClock(), identity=fixed_identity),
        tool_modules=TOOL_MODULES,
    )

    assert_closed_catalog(list_tools(server))


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
