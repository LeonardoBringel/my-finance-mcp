"""Business error raised by domain rules without depending on the MCP SDK."""

import ast
from pathlib import Path

from my_finance_mcp.domain import errors
from my_finance_mcp.domain.errors import ToolInputError
from my_finance_mcp.server import runtime

ERRORS_MODULE = Path(errors.__file__)
FORBIDDEN_IMPORT_ROOTS = ("mcp", "mcp_types", "my_finance_mcp.server")


def _imported_modules(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _is_forbidden(module: str) -> bool:
    return any(
        module == root or module.startswith(f"{root}.")
        for root in FORBIDDEN_IMPORT_ROOTS
    )


def test_runtime_reexports_the_domain_class():
    assert runtime.ToolInputError is ToolInputError


def test_domain_errors_does_not_import_the_mcp_sdk_or_the_server():
    imported = _imported_modules(ERRORS_MODULE)

    assert [module for module in imported if _is_forbidden(module)] == []


def test_only_the_code_goes_to_exception_args():
    error = ToolInputError(
        "UNKNOWN_CATEGORY", "Categoria 'xpto' não encontrada.", "Dica."
    )

    assert error.args == ("UNKNOWN_CATEGORY",)
    assert error.code == "UNKNOWN_CATEGORY"
    assert error.message == "Categoria 'xpto' não encontrada."
    assert error.hint == "Dica."


def test_text_joins_code_message_and_hint():
    error = ToolInputError(
        "UNKNOWN_CATEGORY",
        "Categoria 'merkado' não encontrada.",
        "Categorias parecidas: Mercado.",
    )

    assert error.text == (
        "UNKNOWN_CATEGORY: Categoria 'merkado' não encontrada. "
        "Categorias parecidas: Mercado."
    )


def test_text_without_hint_ends_at_the_message():
    error = ToolInputError("INVALID_PERIOD", "Período inválido.")

    assert error.hint == ""
    assert error.text == "INVALID_PERIOD: Período inválido."
