"""The only data door a tool handler sees, bound to one user and one call."""

from datetime import date
from typing import TYPE_CHECKING, Protocol

from cryptography.fernet import Fernet
from sqlalchemy import Engine

from my_finance_mcp.data.repositories import (
    categories_repository,
    transactions_repository,
)
from my_finance_mcp.data.rows import CategoryCounts, CategoryRow, LoadedRows
from my_finance_mcp.domain.periods import years_touched

if TYPE_CHECKING:
    # Annotation only: the runtime module imports this one.
    from my_finance_mcp.server.runtime import CallCache


class UserData(Protocol):
    def categories(self) -> list[CategoryRow]: ...

    def transactions_between(self, start: date, end: date) -> LoadedRows: ...

    def category_counts(self) -> CategoryCounts: ...

    def available_years(self) -> list[int]: ...

    def installments(self) -> LoadedRows: ...


class DbUserData:
    """Reads one user's data; decrypted rows live only in the call cache."""

    def __init__(
        self, engine: Engine, fernet: Fernet, user_id: int, cache: "CallCache"
    ) -> None:
        self._engine = engine
        self._fernet = fernet
        self._user_id = user_id
        self._cache = cache

    def _year_key(self, year: int) -> tuple[str, int, int]:
        # The prefix keeps these entries apart from other uses of the cache.
        return ("transactions", self._user_id, year)

    def transactions_between(self, start: date, end: date) -> LoadedRows:
        years = years_touched(start, end)
        missing = [y for y in years if self._year_key(y) not in self._cache]
        if missing:
            loaded = transactions_repository.list_by_years(
                self._engine, self._fernet, self._user_id, missing
            )
            for year in missing:
                self._cache[self._year_key(year)] = loaded[year]
        per_year = [self._cache[self._year_key(year)] for year in years]
        # A row is filed by its year column, which may not match its date.
        rows = tuple(
            row
            for year_rows in per_year
            for row in year_rows.rows
            if start <= row.date <= end
        )
        skipped = sum(year_rows.skipped for year_rows in per_year)
        return LoadedRows(rows=rows, skipped=skipped)

    def categories(self) -> list[CategoryRow]:
        return categories_repository.list_categories(
            self._engine, self._fernet, self._user_id
        )

    def category_counts(self) -> CategoryCounts:
        return categories_repository.count_transactions_by_category(
            self._engine, self._user_id
        )

    def available_years(self) -> list[int]:
        return transactions_repository.list_available_years(
            self._engine, self._user_id
        )

    def installments(self) -> LoadedRows:
        return transactions_repository.list_installments(
            self._engine, self._fernet, self._user_id
        )
