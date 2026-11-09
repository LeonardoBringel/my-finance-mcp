"""Explicit SQL reads of the user's categories."""

from cryptography.fernet import Fernet
from sqlalchemy import Engine

from my_finance_mcp.data.crypto import decrypt
from my_finance_mcp.data.rows import CategoryCounts, CategoryRow
from my_finance_mcp.data.sql import read_all
from my_finance_mcp.domain.category_types import flow_of

SELECT_CATEGORIES = (
    "SELECT id, name, type FROM categories WHERE user_id = :user_id"
)
# Joining on the user too sends a transaction pointing at another user's
# category to the uncategorized group (c.id NULL).
COUNT_BY_CATEGORY = (
    "SELECT c.id, COUNT(*) FROM transactions t"
    " LEFT JOIN categories c"
    " ON c.id = t.category_id AND c.user_id = t.user_id"
    " WHERE t.user_id = :user_id GROUP BY c.id"
)


def list_categories(
    engine: Engine, fernet: Fernet, user_id: int
) -> list[CategoryRow]:
    rows = read_all(
        engine, SELECT_CATEGORIES, {"user_id": user_id}, "categories"
    )
    categories = []
    for category_id, name, category_type in rows:
        decrypted_type = decrypt(category_type, fernet)
        categories.append(
            CategoryRow(
                id=category_id,
                name=decrypt(name, fernet) or "",
                type=decrypted_type,
                flow=flow_of(decrypted_type),
            )
        )
    return categories


def count_transactions_by_category(
    engine: Engine, user_id: int
) -> CategoryCounts:
    rows = read_all(
        engine, COUNT_BY_CATEGORY, {"user_id": user_id}, "transactions"
    )
    by_category: dict[int, int] = {}
    uncategorized = 0
    for category_id, count in rows:
        if category_id is None:
            uncategorized = count
        else:
            by_category[category_id] = count
    return CategoryCounts(by_category=by_category, uncategorized=uncategorized)
