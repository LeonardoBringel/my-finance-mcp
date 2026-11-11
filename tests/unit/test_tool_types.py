"""JSON schema of the shared tool argument types."""

import ast
from pathlib import Path

from pydantic import TypeAdapter

from my_finance_mcp.server import tools
from my_finance_mcp.server.tools._types import (
    CategoryTypeFilter,
    EndDate,
    StartDate,
)


def test_start_date_schema_is_a_date_with_its_description():
    schema = TypeAdapter(StartDate).json_schema()

    assert schema["format"] == "date"
    assert schema["description"] == "Início do período, inclusivo."


def test_end_date_schema_is_a_date_with_its_description():
    schema = TypeAdapter(EndDate).json_schema()

    assert schema["format"] == "date"
    assert schema["description"] == "Fim do período, inclusivo."


def test_category_type_filter_accepts_the_four_types_and_null():
    schema = TypeAdapter(CategoryTypeFilter).json_schema()

    enum_branch = next(b for b in schema["anyOf"] if "enum" in b)
    assert sorted(enum_branch["enum"]) == [
        "ambos",
        "entrada",
        "investimento",
        "saida",
    ]
    assert {"type": "null"} in schema["anyOf"]
    assert schema["description"] == (
        "Filtra pelo tipo bruto da categoria. Omitido, retorna todas."
    )


def test_tools_package_does_not_import_the_mcp_sdk():
    source = Path(tools.__file__).read_text()

    imported = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert imported == []
