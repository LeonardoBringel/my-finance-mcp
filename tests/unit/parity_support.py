"""The parity dataset served through the real read path.

The tools read the dataset through DbUserData and the real repositories; only
the engine is fake. It holds the rows encrypted the way my-finance stores
them, so decryption, legacy plain text and unreadable dates go through the
same code as in production.
"""

import json
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import jsonschema
from cryptography.fernet import Fernet
from mcp.server import MCPServer

from my_finance_mcp.auth.identity import AuthenticatedUser
from my_finance_mcp.data.repositories.categories_repository import (
    COUNT_BY_CATEGORY,
    SELECT_CATEGORIES,
)
from my_finance_mcp.data.repositories.transactions_repository import (
    SELECT_AVAILABLE_YEARS,
    SELECT_BY_YEARS,
)
from my_finance_mcp.data.user_data import DbUserData
from my_finance_mcp.server.app import build_mcp_server
from my_finance_mcp.server.runtime import CallCache, ToolRuntime
from tests.unit.db_fakes import FakeConnection, FakeEngine, FakeResult
from tests.unit.test_mcp_server import with_client

PARITY = Path(__file__).resolve().parents[1] / "fixtures" / "parity"
FERNET = Fernet(Fernet.generate_key())


@dataclass(frozen=True)
class Dataset:
    reference_date: date
    user_id: int
    categories: list[dict]
    transactions: list[dict]


def _load(name: str) -> dict:
    return json.loads((PARITY / name).read_text(encoding="utf-8"))


def load_dataset() -> Dataset:
    raw = _load("dataset.json")
    return Dataset(
        reference_date=date.fromisoformat(raw["reference_date"]),
        user_id=raw["user_id"],
        categories=raw["categories"],
        transactions=raw["transactions"],
    )


def load_golden() -> dict:
    return _load("golden.json")


def encrypt(text: str) -> str:
    return FERNET.encrypt(text.encode("utf-8")).decode("ascii")


def _transaction_row(row: dict, categories: dict[int, dict]) -> tuple:
    """The row as SELECT_BY_YEARS returns it, columns in its order."""
    # Rows saved before my-finance encrypted its columns hold plain text.
    seal = str if row["plaintext"] else encrypt
    category = categories.get(row["category_id"])
    return (
        row["id"],
        row["year"],
        seal(row["date"]),
        seal(row["value"]),
        seal(row["description"]) if row["description"] else None,
        category["id"] if category else None,
        encrypt(category["name"]) if category else None,
        encrypt(category["type"]) if category else None,
        row["installment_group"],
        row["installment_number"],
        row["installment_total"],
    )


class _YearFilteringConnection(FakeConnection):
    def execute(self, statement, parameters=None):
        result = super().execute(statement, parameters)
        if str(statement) != SELECT_BY_YEARS:
            return result
        years = set(parameters["years"])
        return FakeResult([row for row in result.all() if row[1] in years])


class YearFilteringEngine(FakeEngine):
    """Serves the dataset, with the transactions of the requested years only.

    The real query filters by year in SQL, and the repository files each row
    under one of the years it asked for, so it cannot take any other year.
    """

    def __init__(self, dataset: Dataset) -> None:
        categories = {c["id"]: c for c in dataset.categories}
        transactions = dataset.transactions
        counts = Counter(row["category_id"] for row in transactions)
        super().__init__(
            {
                SELECT_BY_YEARS: [
                    _transaction_row(row, categories) for row in transactions
                ],
                SELECT_CATEGORIES: [
                    (c["id"], encrypt(c["name"]), encrypt(c["type"]))
                    for c in dataset.categories
                ],
                COUNT_BY_CATEGORY: list(counts.items()),
                SELECT_AVAILABLE_YEARS: [
                    (year,)
                    for year in sorted({r["year"] for r in transactions})
                ],
            }
        )

    @contextmanager
    def connect(self):
        with super().connect():
            yield _YearFilteringConnection(self)


def dataset_user_data(
    cache: CallCache, engine: FakeEngine | None = None
) -> DbUserData:
    dataset = load_dataset()
    if engine is None:
        engine = YearFilteringEngine(dataset)
    return DbUserData(engine, FERNET, dataset.user_id, cache)


def cents(value: float) -> int:
    """Value in whole cents, from its shortest float text, half up."""
    return int(
        Decimal(repr(value)).scaleb(2).quantize(Decimal("1"), ROUND_HALF_UP)
    )


class _ReferenceClock:
    def __init__(self, today: date) -> None:
        self._today = today

    def today(self) -> date:
        return self._today


def parity_server(engine: FakeEngine | None = None) -> MCPServer:
    """Production catalog as the dataset user, today at the golden date."""
    dataset = load_dataset()
    reference = date.fromisoformat(load_golden()["reference_date"])
    runtime = ToolRuntime(
        _ReferenceClock(reference),
        schema_check=lambda: None,
        user_data=lambda user_id, cache: dataset_user_data(cache, engine),
        identity=lambda: AuthenticatedUser(user_id=dataset.user_id, jti="j"),
    )
    return build_mcp_server(runtime)


def call_tool(server: MCPServer, name: str, arguments: dict) -> dict:
    """structuredContent of a successful call, checked against its schema."""

    async def action(client):
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        result = await client.call_tool(name, arguments)
        assert result.is_error is False, result.content
        jsonschema.validate(
            result.structured_content, tools[name].output_schema
        )
        return result.structured_content

    return with_client(server, action)
