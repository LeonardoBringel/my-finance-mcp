"""Category types and the money flow each one counts toward.

The constants are the values my-finance stores (encrypted) in
categories.type, so they must match its code exactly, case included.
"""

from typing import Literal

INCOME = "entrada"
EXPENSE = "saida"
BOTH = "ambos"
INVESTMENT = "investimento"

ALL_TYPES = (EXPENSE, INCOME, INVESTMENT, BOTH)

# Label of the group of transactions without a category.
UNCATEGORIZED_LABEL = "(sem categoria)"

Flow = Literal["saida", "entrada", "investimento"]
CategoryType = Literal["entrada", "saida", "investimento", "ambos"]

_FLOWS: dict[str, Flow] = {
    EXPENSE: "saida",
    # my-finance counts a "both" category as an expense.
    BOTH: "saida",
    INCOME: "entrada",
    INVESTMENT: "investimento",
}


def flow_of(category_type: str | None) -> Flow | None:
    """Flow of a category type; None leaves the transaction out of totals."""
    if category_type is None:
        return None
    return _FLOWS.get(category_type)
