"""The user's categories filtered by type, in a stable order, with counts."""

from collections.abc import Sequence
from dataclasses import dataclass

from my_finance_mcp.data.rows import CategoryCounts, CategoryRow
from my_finance_mcp.domain.categories import normalize_name
from my_finance_mcp.domain.category_types import (
    ALL_TYPES,
    EXPENSE,
    CategoryType,
    Flow,
    display_name,
    flow_of,
)


@dataclass(frozen=True)
class CategoryEntry:
    name: str
    type: CategoryType | None  # None for a type my-finance does not define
    flow: Flow | None
    transaction_count: int


@dataclass(frozen=True)
class CategoryListing:
    entries: tuple[CategoryEntry, ...]
    total_count: int  # before the limit cut
    uncategorized: int | None  # None when the filter excludes expenses
    truncated: bool


def list_categories(
    categories: Sequence[CategoryRow],
    counts: CategoryCounts,
    type_filter: CategoryType | None,
    limit: int = 200,
) -> CategoryListing:
    """Categories of the filter's raw type, or all of them without a filter.

    A category of unknown type has no type to match, so it is listed only
    without a filter; hiding it there would hide its transactions too.
    """
    selected = [
        row
        for row in categories
        if type_filter is None or row.type == type_filter
    ]
    # The original name and the id break ties between names that only differ
    # by case or accents, so the order never depends on the input order.
    selected.sort(key=lambda row: (normalize_name(row.name), row.name, row.id))
    entries = tuple(_entry(row, counts) for row in selected[:limit])
    # Transactions without a category count as expenses.
    uncategorized = (
        counts.uncategorized if type_filter in (None, EXPENSE) else None
    )
    return CategoryListing(
        entries=entries,
        total_count=len(selected),
        uncategorized=uncategorized,
        truncated=len(selected) > limit,
    )


def _entry(row: CategoryRow, counts: CategoryCounts) -> CategoryEntry:
    category_type = row.type if row.type in ALL_TYPES else None
    return CategoryEntry(
        name=display_name(row.name),
        type=category_type,
        flow=flow_of(category_type),
        transaction_count=counts.by_category.get(row.id, 0),
    )
