"""Category type to money flow mapping, mirrored from my-finance."""

import pytest

from my_finance_mcp.domain.category_types import UNCATEGORIZED_LABEL, flow_of


@pytest.mark.parametrize(
    ("category_type", "expected"),
    [
        ("saida", "saida"),
        # A category marked "both" counts as an expense in my-finance.
        ("ambos", "saida"),
        ("entrada", "entrada"),
        ("investimento", "investimento"),
    ],
)
def test_known_types_map_to_their_flow(category_type, expected):
    assert flow_of(category_type) == expected


@pytest.mark.parametrize("category_type", [None, "", "Saida", "outro"])
def test_empty_or_unknown_type_has_no_flow(category_type):
    assert flow_of(category_type) is None


def test_uncategorized_label():
    assert UNCATEGORIZED_LABEL == "(sem categoria)"
