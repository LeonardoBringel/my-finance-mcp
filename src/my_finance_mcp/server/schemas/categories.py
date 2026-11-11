"""Output of the list_categories tool."""

from pydantic import BaseModel, Field

from my_finance_mcp.domain.category_types import CategoryType, Flow


class CategoryItem(BaseModel):
    name: str
    type: CategoryType | None
    flow: Flow | None
    transaction_count: int


class CategoriesData(BaseModel):
    categories: list[CategoryItem]
    total_count: int
    # Left out of the output, not sent as null, when the type filter excludes
    # expenses: transactions without a category only count as expenses.
    uncategorized_transaction_count: int | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
