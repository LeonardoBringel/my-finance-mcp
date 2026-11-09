"""Decrypted rows the repositories return; never driver rows or ORM objects."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from my_finance_mcp.domain.category_types import Flow
from my_finance_mcp.server.schemas.envelope import WarningCode


@dataclass(frozen=True)
class CategoryRow:
    id: int
    name: str  # "" when the token decrypts to an empty text
    type: str | None  # raw decrypted type, possibly unknown
    flow: Flow | None


@dataclass(frozen=True)
class TransactionRow:
    id: int
    year: int
    date: date
    value: Decimal
    description: str | None
    # None also when the category belongs to another user.
    category_id: int | None
    category_name: str  # "(sem categoria)" when category_id is None
    category_type: str | None
    flow: Flow | None  # "saida" without category; None for an unknown type
    installment_group: str | None
    installment_number: int | None
    installment_total: int | None


@dataclass(frozen=True)
class LoadedRows:
    rows: tuple[TransactionRow, ...]
    skipped: int  # rows dropped because their date could not be read

    @property
    def warnings(self) -> tuple[WarningCode, ...]:
        return ("SKIPPED_INVALID_ROWS",) if self.skipped > 0 else ()


@dataclass(frozen=True)
class CategoryCounts:
    by_category: dict[int, int]  # only the user's own categories
    uncategorized: int  # no category, or a category of another user
